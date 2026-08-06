# M1 Technical Plan — `hsql`, the headless CLI

Implementation plan for milestone M1 of [Harlequin for agents](./harlequin-for-agents.md).
That document is the product spec: what `hsql` is, why it's a separate command, and what
its contract with an agent looks like. This one is about how we build it, what has to be
refactored first, and in what order it can ship.

**M1's scope, from the roadmap:** second console script; extract the shared execution
core; import-linter rule and cold-start benchmark in CI; `-c`, `-f`, stdin, `-o`, `-F` +
shorthands, default `--limit 500`, truncation notices, `--stats`, exit codes,
`--on-error`, `--color`/`NO_COLOR`, plain errors; unknown-option hint on `harlequin`;
the seeded "Headless & Agents" docs topic. Closes
[#524](https://github.com/tconbeer/harlequin/issues/524).

---

## 1. Where the code actually is today

Everything below was measured on this repo at `2.8.0`, not estimated.

### 1.1 There is no execution core to share

Query execution lives entirely inside the Textual app, as two threaded workers that
communicate by posting messages:

- `Harlequin._execute_query` (`src/harlequin/app.py:1077`) — loops over statements,
  calls `connection.execute(q)`, applies `cur.set_limit(...)`, collects cursors, posts
  `QueriesExecuted`.
- `Harlequin._fetch_data` (`src/harlequin/app.py:1154`) — drains each cursor with
  `fetchall()` + `columns()`, times the batch, posts `ResultsFetched`.

There is no importable function. `hsql` cannot call any of this, and a naive copy is
exactly the "two products" failure §12 of the product plan warns about.

The split into *execute all, then fetch all* is deliberate and good — it's what lets the
TUI show "query executed" before results materialize. The extracted core should preserve
that shape rather than flatten it, so the TUI can adopt it without changing its
concurrency model.

`Harlequin._split_query_text` (`app.py:1135`) is a naive `text.split(";")`. **It is dead
code** — nothing calls it. The live splitter is `CodeEditor.selected_queries()`
(`components/code_editor.py:36`), which uses tree-sitter via textual-textarea and is
therefore both correct and unavailable outside a running app. See §3.2.

### 1.2 Cold start is 1.3 seconds, and the reason is structural

```
$ time harlequin --version
harlequin, version 2.8.0
real    0m1.335s
```

That is the cheapest possible invocation of the current CLI. The 300ms target in the
product plan is not a matter of shaving milliseconds; four separate things each pull in
the whole TUI:

| # | Cause | Cost |
| --- | --- | --- |
| 1 | `harlequin/__init__.py` imports `harlequin.app` at module scope, so importing *any* `harlequin.*` submodule imports Textual, sqlfmt, prompt_toolkit and pyarrow | 660ms floor |
| 2 | `harlequin/adapter.py:7` imports `textual_fastdatatable.backend` for `AutoBackendType`, which is `Any` | 265ms |
| 3 | `harlequin/options.py` imports `questionary`, `textual.validation`, `textual.widget` and `harlequin.copy_widgets` at module scope, for `to_questionary()` and `to_widgets()` | 187ms |
| 4 | `build_cli()` calls `load_adapter_plugins()`, which does `ep.load()` on **every installed adapter** to reach its `ADAPTER_OPTIONS` | 160ms for 4 adapters, unbounded |

Cause 2 recurs in the adapters themselves: `harlequin_duckdb/adapter.py:14` and
`harlequin_sqlite/adapter.py` both import `textual_fastdatatable.backend` at module
scope, so even a perfectly clean `hsql` would pay for Textual the moment it loaded an
adapter.

`harlequin/catalog.py:6` imports `textual.message.Message` for `NewCatalog` and
`NewCatalogItems` — two TUI-only messages sitting in a module every adapter imports.

**I prototyped the fix and measured it.** With the package `__init__` made lazy
(PEP 562 `__getattr__`), `AutoBackendType` moved under `TYPE_CHECKING` in core and in
both first-party adapters, and the widget/questionary imports deferred into
`to_widgets()` / `to_questionary()`:

```
headless core (adapter + options + config + plugins + rich_click):
    115ms   textual=False  questionary=False  238 modules
+ harlequin_duckdb:
     56ms   textual=False
```

~171ms plus ~16ms of interpreter startup. Against a floor of 161ms for the runtime
dependencies `hsql` genuinely needs (click, tomlkit, platformdirs, duckdb, pyarrow),
that leaves real headroom under 300ms. `from harlequin import Harlequin` still works
after the change, which matters — it's the documented import for every third-party
adapter.

Cause 4 is not fixed by import hygiene; see §3.4.

### 1.3 Two places already write results to stdout that shouldn't

- `harlequin/plugins.py:54` — a bare `print()` when a plug-in fails to import. Under
  `hsql -F csv > out.csv` that lands in the CSV.
- `harlequin/exception.py:68` `pretty_print_warning` — uses `rich.print`, i.e. stdout.
  Reached from `locale_manager.py` and `windows_timezone.py`, both on the CLI path.

`pretty_print_error` already gets this right, with a comment saying why. The contract
exists in intent; M1 makes it true.

### 1.4 Export is coupled to a widget

`harlequin/export.py:20` `copy()` takes a `ResultsTable` (a Textual widget) and reaches
into `table.backend.source_data`. The five exporters underneath it are pure
`pa.Table → file`. The column-deduplication step in between is data logic wearing a
widget's clothes.

---

## 2. The four obstacles, stated plainly

1. **No execution core exists.** It has to be extracted from the app before `hsql` can
   have one, or we ship two implementations on day one.
2. **Every import path leads to Textual.** Fixable, cheap, and measured — but it must
   land before anything else, because it's also the guard that keeps the fix from
   silently regressing.
3. **Building the CLI requires importing every installed adapter.** The only unbounded
   cost, and the only one that gets worse as the ecosystem grows.
4. **Result rendering doesn't exist at all.** The TUI renders into a DataTable widget;
   `export.py` writes columnar files via duckdb. Neither is a text formatter.

---

## 3. Target architecture

Five new or reshaped libraries. Each owns a domain and has a stated interface; none of
them is a bag of helpers.

```
harlequin/
  query.py        NEW  execute SQL, get results, without a UI
  formats.py      NEW  render a result set as text, incrementally
  export.py       MOD  render a result set as a columnar file  (widget coupling removed)
  config.py       MOD  + merging CLI values over a profile
  plugins.py      MOD  + naming and loading one adapter without importing the rest
  options.py      MOD  renderer imports deferred; declaration stays import-light
  hsql/           NEW  the command
    __init__.py        main()
    cli.py             the click command and its two-phase adapter resolution
    diagnostics.py     everything written to stderr, and the code we exit with
```

The one-line statement of the architecture, which is also a testable rule:

> **stdout is written only by `harlequin.formats` and `harlequin.export`. stderr is
> written only by `harlequin.hsql.diagnostics`.**

### 3.1 `harlequin.query` — the execution core

```python
@dataclass(frozen=True)
class Statement:
    sql: str
    index: int                 # 0-based position in the submitted script

@dataclass(frozen=True)
class RowLimit:
    max_rows: int | None       # None = unlimited
    detect_overflow: bool      # fetch one extra row so truncation is knowable

@dataclass
class ExecutedStatement:
    statement: Statement
    cursor: HarlequinCursor | None    # None for DDL/DML

@dataclass
class ResultSet:
    statement: Statement
    columns: list[tuple[str, str]]
    row_count: int
    truncated: bool
    elapsed: float
    def rows(self) -> Iterator[tuple[Any, ...]]: ...

def split_statements(script: str) -> list[Statement]: ...
def execute(
    connection: HarlequinConnection,
    statements: Sequence[Statement],
    limit: RowLimit,
    on_error: Literal["stop", "continue"] = "stop",
) -> Iterator[ExecutedStatement]: ...
def fetch(executed: ExecutedStatement, limit: RowLimit) -> ResultSet: ...
```

**`RowLimit` exists because truncation cannot otherwise be detected.** `set_limit(n)`
followed by `fetchall()` returns *at most* n rows and tells you nothing about whether an
n+1th existed. Getting exactly n rows is ambiguous. The only portable answer is to
request `n + 1` and emit `n` — which is why the limit is a value object with an explicit
`detect_overflow`, rather than an `int` that each caller handles its own way. It costs
one row. Principle 5 of the product plan ("truncation must always be announced — never
silent") is unimplementable without it.

**`ResultSet.rows()` is where adapter output gets normalized.** `HarlequinCursor.fetchall()`
returns `AutoBackendType`, which is `Any` — in practice a `pa.Table` (duckdb), a
`RecordBatch`, a `Sequence[Iterable]` (sqlite), a `Mapping[str, Sequence]`, or a polars
or pandas frame. Today `textual_fastdatatable.create_backend()` does this normalization,
and it costs 265ms and imports Textual. `hsql` needs its own, and it belongs next to the
result type rather than inside a formatter, so that every format gets identical row
semantics. Deliberately an `Iterator`: it is the seam that lets M5 turn `ResultSet` into
a stream of batches without touching any formatter.

The TUI's two workers become thin wrappers: `_execute_query` calls `execute()`,
`_fetch_data` calls `fetch()`, both still post the same messages. Both keep their
`AutoBackendType` payload for the DataTable — the TUI never calls `rows()`.

### 3.2 Statement splitting

`hsql -f script.sql` needs to split SQL outside a running Textual app, and `;` inside a
string literal is not hypothetical in real scripts.

Three options: naive `;` split (wrong), reuse sqlfmt's lexer (already a dependency,
~85ms, polyglot), or a conservative hand-rolled scanner that understands single and
double quotes, dollar-quoting, and line and block comments (~60 lines, no new import,
covers everything a script realistically contains).

**Recommendation: spike sqlfmt's lexer first; fall back to the scanner.** sqlfmt is
already a hard dependency and already maintained for exactly this class of problem, so
if its analyzer can hand us top-level semicolon positions, that's strictly better than a
second SQL scanner in this codebase. Budget half a day; if the answer isn't clean, write
the scanner.

**The TUI keeps its tree-sitter splitter.** It's more accurate and it's already there.
That means two splitters, which is a drift risk — mitigated concretely by a **shared
fixture corpus of tricky SQL** that both are tested against, so they can't diverge
without a test failing. I'd rather have one honest, tested divergence than force the TUI
onto a worse splitter for the sake of a symmetry nobody benefits from.

### 3.3 `harlequin.formats` — text rendering

```python
@dataclass(frozen=True)
class RenderOptions:
    header: bool = True
    footer: bool = True
    aligned: bool = True
    null_string: str | None = None    # None = per-format default
    color: bool = False

class ResultWriter(Protocol):
    def write_result(self, result: ResultSet, out: TextIO) -> None: ...

def get_writer(name: str, options: RenderOptions) -> ResultWriter: ...
def format_names() -> list[str]: ...
```

Formats: `table`, `markdown`/`md`, `csv`, `tsv`, `json`, `jsonl`/`ndjson`, `vertical`,
`none`. Columnar formats (`parquet`, `arrow`, `orc`) route to `export.py` instead, which
needs a path and a materialized table.

`RenderOptions` is what makes psql's flag algebra fall out for free: `-t` is
`header=False, footer=False`, `-A` is `aligned=False`, and `-tA` needs no special case
because it was never a special case — it's two independent options, which is exactly how
psql users already think about it.

**`table` cannot stream** — column widths require seeing every row. `csv`, `tsv`,
`jsonl`, `vertical` can. That's inherent, not a defect; the Protocol is defined over a
`ResultSet` whose `rows()` is an iterator, so the streaming formats are already written
in the shape M5 needs.

**Newlines are part of the contract.** Python text mode translates `\n` to `\r\n` on
Windows, which would make `hsql -F csv` produce different bytes per platform and break
determinism (principle 4). Every writer opens with `newline=""` and emits `\n`, and
there's a test for it.

**One accepted overlap:** `export.py` writes CSV via duckdb for the TUI's export dialog;
`formats.py` writes CSV via the stdlib for `hsql`. Two CSV writers. Unifying them means
either making the TUI's export dialog options (compression, quoting, encoding) part of
the streaming path or dropping them, and neither is M1's business. Noted so it's a
decision rather than an accident; revisit in M5.

### 3.4 `harlequin.plugins` — naming an adapter without importing it

```python
def adapter_names() -> list[str]: ...                        # entry-point names only
def load_adapter(name: str) -> type[HarlequinAdapter]: ...   # imports exactly one
def load_adapter_plugins() -> dict[str, type[HarlequinAdapter]]: ...   # existing
```

`adapter_names()` reads entry-point *names* without calling `ep.load()`, so it costs
nothing. That's what makes the two-phase parse in §3.6 possible, and it's the fix for
obstacle 3.

### 3.5 `harlequin.config` — merging CLI values over a profile

The merge in `cli.py:315-327` (drop every parameter whose click source is `DEFAULT`,
then `config.update(kwargs)`) is the precedence rule both commands must implement
identically. It moves to `config.py`, which already owns `Profile`:

```python
def merge_profile_with_cli(
    profile: Profile,
    cli_values: Mapping[str, Any],
    explicitly_set: Container[str],
) -> Profile: ...
```

`explicitly_set` rather than a click `Context`, so the config library stays free of the
CLI framework and is testable without one.

Adapter *instantiation* stays in each command — it's five lines, and the two commands
handle its failure differently (Rich panel and exit 2 vs. a plain line and exit 2). We
share the precedence rule, which is the part that would drift; we don't share five lines
of error handling that legitimately differ.

### 3.6 `harlequin.hsql` — the command

Two-phase parse, which is what obstacle 3 forces and what makes `hsql --help` cheap:

1. Parse with `resilient_parsing` to find `-a/--adapter` and `-P/--profile`, using
   `adapter_names()` — no adapter imported.
2. Resolve the profile (a profile may name an adapter), pick the one adapter in play,
   `load_adapter(name)`, and build the real command with only that adapter's options.

An invocation only ever uses one adapter, so this is not a compromise — and
`hsql --help` showing one adapter's connection options instead of all four is *better*
for an agent, not worse. `hsql --help -a postgres` shows postgres'; `hsql info --json`
(M2) enumerates everything.

This contradicts one line in the product plan (§4: "It still carries the full adapter
connection-option matrix; that's unavoidable and shared"). It turns out to be avoidable,
and avoiding it is both a token saving and the fix for the unbounded startup cost. See
§8.

`harlequin` itself keeps its current all-adapters help. Changing it would alter published
`--help` output and the docs built from it, for no benefit to a TUI that's already paying
for Textual.

`diagnostics.py` owns the stderr contract:

```python
class ExitCode(IntEnum):
    OK = 0; QUERY = 1; USAGE = 2; CONNECTION = 3; TIMEOUT = 4; INTERRUPT = 130

def exit_code_for(error: BaseException) -> ExitCode: ...
def report_error(error: BaseException) -> None:          # "hsql: error: ..."
def report_truncation(result: ResultSet, limit: RowLimit) -> None:
def report_stats(...) -> None:                            # one line of JSON
```

Two things worth stating because they're easy to get wrong:

- **The truncation notice fires under `-t`.** `-t` suppresses stdout chrome; it does not
  suppress warnings. A flag that silently defeats truncation reporting would undo the
  principle it's meant to coexist with.
- **Exit codes are hsql's contract, not Harlequin's**, so the mapping lives here rather
  than in `harlequin.exception`. `HarlequinQueryError → 1`, `HarlequinConnectionError → 3`,
  `HarlequinConfigError → 2`, `KeyboardInterrupt → 130`.

---

## 4. Sequencing

Two releases. The first contains no new user-facing surface at all; the second is the
feature. Every PR in the first release is independently reviewable, independently
testable, and behavior-neutral for the TUI.

### Release A — the plumbing (2.9)

**PR 1 — Import hygiene, and the guard that keeps it.**
Lazy `harlequin/__init__.py` via PEP 562 `__getattr__`, preserving every current name.
`AutoBackendType` under `TYPE_CHECKING` in `adapter.py`, `harlequin_duckdb`,
`harlequin_sqlite`. `NewCatalog`/`NewCatalogItems` move to `harlequin/messages.py`, with
a module-level `__getattr__` shim in `catalog.py` so existing imports keep working
without importing Textual. Deferred renderer imports in `options.py`; `_CustomValidator`
moves to `copy_widgets.py`, which is already the option-widgets library. Fix the two
stdout leaks from §1.3. Add import-linter contracts and the cold-start benchmark script.

Ships value on its own: importing `harlequin_duckdb` goes from 691ms to 56ms, which
every adapter's test suite and every library consumer feels.

**PR 2 — `harlequin.query`.** Extract the core; refactor `_execute_query` and
`_fetch_data` onto it; delete the dead `_split_query_text`; land the splitter and its
tricky-SQL corpus. Snapshot tests are the safety net for the TUI refactor.

**PR 3 — `harlequin.formats`, and `export.py` decoupled from `ResultsTable`.** Golden
files per format. No user-visible change; `formats.py` has no consumer until PR 5.

**PR 4 — `merge_profile_with_cli`, `adapter_names`, `load_adapter`.** `harlequin`'s own
CLI refactored onto the first; behavior-neutral, covered by the existing 392 lines of
`test_cli.py`.

*Why these land on main rather than accumulating on a feature branch:* they're
behavior-neutral and individually tested, and the alternative is a long-lived branch
rebasing against a moving TUI — which is where extraction refactors go to die.

### Release B — the command (2.10)

**PR 5 — `hsql`.** Console script, two-phase parse, `-c`/`-f`/stdin, `-o`, `-F` and the
shorthands, `-t`/`-A`/`--no-header`/`--null-string`, `-P`, `-l` defaulting to 500,
`--result`, `--on-error`, `--color`/`NO_COLOR`, `--stats`, exit codes, truncation
notices, plain errors.

**PR 6 — Discoverability.** Unknown-option handler on `harlequin` (`-c` → "did you mean
`hsql -c`?"), and the `-t nord` hint on `hsql` when an unparseable conn_str matches a
known theme name.

**PR 7 — Docs.** The "Headless & Agents" topic (a PR against `tconbeer/harlequin-web`),
README, changelog.

**PR 8 — The agent eval suite.** Three tasks against fixture databases, scored on
wrong-flag retries and completion. §13 of the product plan calls this the metric that
actually matters; three tasks is enough to start and to expand against.

**Ordering rationale.** Everything the output contract touches ships at once, in PR 5.
The product plan's premise is that format and exit codes are a frozen API — a
half-shipped `hsql -c` that we "improve later" is the one thing here that can't be
undone. Internals can be improved after release; the contract can't.

---

## 5. Testing

**Deterministic guards, not timing assertions.** The cold-start number is the headline,
but a wall-clock assertion on a shared CI runner is a flaky test that gets deleted within
a month. So:

- *Deterministic and blocking:* import-linter contracts — the `hsql` module graph must
  not contain `textual`, `questionary`, or `textual_fastdatatable`. Plus a subprocess
  test asserting `"textual" not in sys.modules` after importing the headless core. Both
  fail identically on every machine.
- *Informational:* the benchmark reports `hsql -c "select 1"` against DuckDB into the
  job summary, and fails only past a loose ceiling (600ms) that catches a real
  regression without catching a busy runner.

The import contract is the load-bearing one. Timing is downstream of it.

Beyond that:

- **`CliRunner` tests** for `hsql`, following the existing `test_cli.py` patterns and
  its adapter-mocking fixtures.
- **Golden files** per format against a fixture result set with nulls, unicode, wide
  values, duplicate column names, and zero rows. Zero rows is the case that separates
  "the query returned nothing" from "the query failed" — A3 in the product plan — and
  it's the one most likely to be wrong in a format nobody exercised.
- **stdout purity:** for each format, assert stdout bytes are identical with and without
  `--stats`, and that a query error leaves stdout completely empty.
- **Determinism:** identical bytes whether stdout is a pipe, a file, or a pty; `\n`
  regardless of platform.
- **`hsql -tAc "select count(*)"` returning a bare number and nothing else** as its own
  named test. It's the idiom the scripting audience will try first.
- **Truncation:** exactly-at-limit, one-over-limit, and under-limit, on both duckdb and
  sqlite — the two adapters implement `set_limit` differently enough to matter.
- **The tricky-SQL corpus** against both splitters.

---

## 6. Decisions I need from you

1. **Should the TUI adopt overflow detection?** The core can tell the TUI that its
   100,000-row limit bit, and it could show "100,000+ rows" instead of "100,000 rows".
   More honest, costs one row, but it's a visible TUI change and it moves snapshots. My
   inclination is yes, in PR 2 — but it's your product. Default if you don't care: leave
   the TUI's display alone, `detect_overflow=False` there.
2. **`hsql --help` showing only the selected adapter's options** — confirmed as an
   improvement rather than a regression? It's what makes the startup cost bounded, and
   I'd want to make the same call even if it weren't.
3. **`--result` default.** The product plan says `all` for text formats and `last` for
   data formats. That's context-dependent behavior, which cuts against determinism. I'd
   rather default to `all` everywhere and let `json`/`csv` emit multiple documents when a
   script produces multiple result sets — but "csv with two headers in it" is genuinely
   awkward, so the plan's version may be the lesser evil. Weak opinion.
4. **Splitter spike** — half a day on sqlfmt's lexer before hand-rolling. Confirm that's
   worth the time, or say "just write the scanner" and I'll write the scanner.

---

## 7. Explicitly not in M1

`--read-only`, `--timeout`, `--dry-run`, `--single-transaction` (M2 — the first three
need adapter-interface additions and an ecosystem rollout). Every subcommand: `catalog`,
`describe`, `fmt`, `spec`, `info`, `config`, `history`, `open`, `mcp`. Real streaming
(M5 — designed for, not built). A separate lean `hsql` distribution (§4 of the product
plan: near-reversible, indefinitely deferrable).

Bare `hsql` with no arguments prints help and never launches the TUI, from the first
release.

---

## 8. Corrections to the product plan

- **§4 says the full adapter option matrix on `hsql --help` is "unavoidable and shared."**
  It's avoidable, via the two-phase parse in §3.6, and avoiding it is both a token saving
  and the fix for the only unbounded startup cost. The product plan should be amended.
- **§12 lists cold start as a risk with "may need lazy entry-point resolution."** It's not
  a maybe. `load_adapter_plugins()` imports every installed adapter on every invocation —
  160ms with four installed, growing without bound. Lazy resolution is a requirement of
  M1, not a contingency.
- **§5's truncation guarantee needs the limit+1 fetch to be implementable at all.** Worth
  stating in the product plan, because it's the kind of thing that gets designed out as
  an implementation detail and then quietly breaks the promise.
