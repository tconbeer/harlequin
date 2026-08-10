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

**Bottom line up front.** A working prototype of the refactors below runs a
`select … union all select …` against DuckDB — interpreter startup, config discovery,
adapter load, connect, execute, fetch, normalize, render — in **220–242ms with Textual
never imported**, down from a 1335ms floor today. Nothing in M1 needs new normalization
or parsing code: the two hard pieces already exist in `textual-fastdatatable` and
`tree-sitter-sql`, and both can be reached without Textual.

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
(`components/code_editor.py:36`), which uses tree-sitter through textual-textarea. See
§3.2 — the grammar it uses is reachable without any of that.

### 1.2 Cold start is 1.3 seconds, and the reasons are structural

```
$ time harlequin --version
harlequin, version 2.8.0
real    0m1.335s
```

That is the cheapest possible invocation of the current CLI. The 300ms target is not a
matter of shaving milliseconds; five separate things each pull in the whole TUI:

| # | Cause | Cost |
| --- | --- | --- |
| 1 | `harlequin/__init__.py` imports `harlequin.app` at module scope, so importing *any* `harlequin.*` submodule imports Textual, sqlfmt, prompt_toolkit and pyarrow | 660ms floor |
| 2 | `harlequin/adapter.py:7` imports `textual_fastdatatable.backend` for `AutoBackendType`, which is `Any` | 265ms |
| 3 | `textual_fastdatatable/__init__.py` imports the `DataTable` widget, so the backend can't be reached without Textual | 58ms + 158 modules of the 265ms above |
| 4 | `harlequin/options.py` imports `questionary`, `textual.validation`, `textual.widget` and `harlequin.copy_widgets` at module scope, for `to_questionary()` and `to_widgets()` | 187ms |
| 5 | `build_cli()` calls `load_adapter_plugins()`, which does `ep.load()` on **every installed adapter** to reach its `ADAPTER_OPTIONS` | 160ms for 4 adapters, unbounded |

Cause 2 recurs in the adapters themselves: `harlequin_duckdb/adapter.py:14` and
`harlequin_sqlite/adapter.py` both import `textual_fastdatatable.backend` at module
scope, so even a perfectly clean `hsql` would pay for Textual the moment it loaded an
adapter.

`harlequin/catalog.py:6` imports `textual.message.Message` for `NewCatalog` and
`NewCatalogItems` — two TUI-only messages sitting in a module every adapter imports.
Neither is used outside `app.py` and `components/data_catalog/database_tree.py`.

### 1.3 `textual-fastdatatable` is ours, and its backend has no Textual in it

We control this package, which changes the answer to the hardest problem in M1 (§3.1).
Measured on the installed 0.16.0:

- `backend.py`, `format.py`, and `column.py` contain **zero references to Textual**. The
  imports are pyarrow and rich.
- The only thing that pulls Textual is `textual_fastdatatable/__init__.py` importing
  `data_table.DataTable`.
- With that `__init__` made lazy: `from textual_fastdatatable.backend import create_backend`
  drops from 265ms/359 modules/Textual to **217ms/201 modules/no Textual**.
- Most of the remainder is `pyarrow.parquet`, imported at module scope but used only by
  `ArrowBackend.from_parquet`. Deferring it into that classmethod takes the import to
  **~140ms/183 modules**.
- `textual>=7.3.0` is a *required* dist dependency. Nothing in the backend uses it. A
  future lean `hsql` install would need that made an extra.

### 1.4 tree-sitter is already installed, and usable without Textual

`textual-textarea` requires `textual[syntax]`, which brings `tree-sitter` and
`tree-sitter-sql`. The editor's separator query is one line
(`code_editor.py:22`): `(";" @semicolon)`.

Driving that grammar directly, with no Textual and no textual-textarea, measures at
**28ms to import, 92 modules**, and 22ms to split a 2000-statement script. It gets
string literals, line comments, block comments, quoted identifiers and unicode right.
It gets `$$`-dollar-quoting wrong — and so does the TUI today, because it's the same
grammar and the same query.

Two further things fell out of driving it directly. Tree-sitter is used in exactly one
place in Harlequin — that one query — and nowhere else. And its `Point.column` is a
**byte** offset, which the editor feeds to a function expecting characters. See §3.2.

### 1.5 Two places already write results to stdout that shouldn't

- `harlequin/plugins.py:54` — a bare `print()` when a plug-in fails to import. Under
  `hsql -F csv > out.csv` that lands in the CSV.
- `harlequin/exception.py:68` `pretty_print_warning` — uses `rich.print`, i.e. stdout.
  Reached from `locale_manager.py` and `windows_timezone.py`, both on the CLI path.

`pretty_print_error` already gets this right, with a comment saying why. The contract
exists in intent; M1 makes it true.

### 1.6 Export is coupled to a widget

`harlequin/export.py:20` `copy()` takes a `ResultsTable` (a Textual widget) and reaches
into `table.backend.source_data`. The five exporters underneath it are pure
`pa.Table → file`. The column-deduplication step in between is data logic wearing a
widget's clothes.

### 1.7 The prototype

I applied the §1.2 fixes plus the §1.3 upstream fixes and ran a script that does what
`hsql -c` will do — config discovery, single-adapter load, connect, execute with
`set_limit(limit + 1)`, `create_backend(data, max_rows=limit)`, write CSV to stdout:

```
220ms  238ms  242ms      textual=False   306 modules
```

With `load_adapter_plugins()` left as-is (all four installed adapters imported) the same
script takes 377–398ms — which is cause 5 measured in situ, and the reason §3.4 is not
optional. `from harlequin import Harlequin` and `from textual_fastdatatable import DataTable`
both still work after the changes; those are load-bearing for third-party adapters and
for the TUI respectively. All patches were reverted.

---

## 2. The four obstacles, stated plainly

1. **No execution core exists.** It has to be extracted from the app before `hsql` can
   have one, or we ship two implementations on day one.
2. **Every import path leads to Textual** — in this repo *and* one level upstream.
   Fixable, cheap, measured; it must land first, because it's also the guard that keeps
   the fix from silently regressing.
3. **Building the CLI requires importing every installed adapter.** The only unbounded
   cost, and the only one that gets worse as the ecosystem grows.
4. **Text layout doesn't exist, and is coupled to a widget where it does.** The TUI
   renders into a DataTable; `export.py` writes files but only from a `ResultsTable`.
   Nothing arranges a result as `table`, `markdown` or `vertical` text.

Note what is *not* on this list, though earlier drafts of this plan had all three:
normalizing adapter output, parsing SQL into statements, and serializing values. Each
looked like a new subsystem and turned out to be an existing one we already own —
`create_backend`, `tree-sitter-sql`, and duckdb respectively. The residual work in M1 is
smaller than it first appears, and the parts that remain are the parts that are genuinely
ours: the CLI, the execution flow, and three text layouts.

---

## 3. Target architecture

Six new or reshaped libraries, plus one upstream change. Each owns a domain and has a
stated interface; none of them is a bag of helpers.

```
textual-fastdatatable
  __init__.py     MOD  lazy DataTable, so `backend` is reachable without Textual
  backend.py      MOD  pyarrow.parquet deferred into from_parquet()

harlequin/
  statements.py   NEW  locating statement boundaries in SQL text
  query.py        NEW  execute SQL, get results, without a UI
  layout.py       NEW  arranging already-serialized text for a reader
  export.py       MOD  writing a result set to a file, serialized by duckdb/pyarrow
  config.py       MOD  + merging CLI values over a profile
  plugins.py      MOD  + naming and loading one adapter without importing the rest
  options.py      MOD  renderer imports deferred; declaration stays import-light
  hsql/           NEW  the command
    __init__.py        main()
    cli.py             the click command and its two-phase adapter resolution
    diagnostics.py     everything written to stderr, and the code we exit with
```

The one-line statement of the architecture, which is also a testable rule:

> **stdout is written only by `harlequin.layout` and `harlequin.export`. stderr is
> written only by `harlequin.hsql.diagnostics`.**

### 3.1 `harlequin.query` — the execution core, over the existing backend

```python
@dataclass(frozen=True)
class Statement:
    sql: str
    index: int  # 0-based position in the submitted script


@dataclass(frozen=True)
class RowLimit:
    max_rows: int | None  # None = unlimited
    detect_overflow: bool  # fetch one extra row so truncation is knowable


@dataclass
class ExecutedStatement:
    statement: Statement
    cursor: HarlequinCursor | None  # None for DDL/DML


@dataclass
class ResultSet:
    statement: Statement
    columns: list[tuple[str, str]]  # (name, short type) from cursor.columns()
    backend: DataTableBackend  # from textual_fastdatatable
    truncated: bool
    elapsed: float

    @property
    def row_count(self) -> int: ...  # backend.row_count
    def rows(self) -> Iterator[Sequence[Any]]: ...
    def text_columns(self) -> pa.Table: ...  # every value CAST AS VARCHAR; see §3.3


def execute(
    connection: HarlequinConnection,
    statements: Sequence[Statement],
    limit: RowLimit,
    on_error: Literal["stop", "continue"] = "stop",
) -> Iterator[ExecutedStatement]: ...
def fetch(executed: ExecutedStatement, limit: RowLimit) -> ResultSet: ...
```

**`ResultSet` wraps a `DataTableBackend` rather than reimplementing one.**
`HarlequinCursor.fetchall()` returns `AutoBackendType`, which is `Any` — in practice a
`pa.Table` (duckdb), a `RecordBatch`, a `Sequence[Iterable]` (sqlite), a
`Mapping[str, Sequence]`, or a polars or pandas frame. `create_backend()` already
normalizes all of those, it is the normalization the TUI uses, and per §1.3 it needs no
Textual to run. Writing a second normalizer for `hsql` would put the most drift-prone
code in the codebase — "what counts as a row, what counts as null, how does a nested
struct come out" — in two places, and the disagreement would surface as `hsql` and the
TUI showing different data for the same query. That is the worst possible place for
these two front doors to diverge.

**Harlequin already has two different limits, and `hsql` wants the other one.** This is
worth spelling out, because the names collide and I had it wrong in an earlier draft:

| | What it does | Where |
| --- | --- | --- |
| **Soft, display** — `harlequin --limit`, `max_results`, default 100,000 | Fetches *everything*, caps what's loaded into the viewer | `results_viewer.py:147`, `create_backend(max_rows=…)` |
| **Hard, fetch** — the Run Query Bar's limit checkbox | `cur.set_limit(n)`, so fewer rows leave the database | `app.py:1093` |

The soft limit is why the TUI needs no overflow detection and never did: because it
fetched every row, it knows the exact total and already says so —
`"(Showing 100,000 of 3,412,887 Records)"` at `results_viewer.py:234`. That's strictly
better than anything an inference could give it. My earlier suggestion that it show
"100,000+" was wrong.

**`hsql -l` is the hard limit.** Fetching a million rows to print five hundred would
defeat the entire point of a limit for an agent — time, memory, and egress. There is also
no viewport headless, so the soft limit has no meaning: `hsql` has exactly one limit
concept, and it's the fetch-side one. Same flag name, different semantics between the two
commands; the docs need one sentence about that rather than a pretense that they match.

**Which is why truncation needs `limit + 1`.** `set_limit(n)` then `fetchall()` returns at
most n rows and tells you nothing about whether an n+1th existed; exactly n is ambiguous.
So `execute()` requests `limit + 1` and `fetch()` calls `create_backend(data, max_rows=limit)`.
`DataTableBackend` already distinguishes `row_count` (capped) from `source_row_count`
(what it was handed), so `truncated = source_row_count > row_count` — the mechanism
exists, we just feed it one extra row. This is why the limit is a value object with an
explicit `detect_overflow` rather than an `int` each caller interprets: principle 5 of the
product plan ("truncation must always be announced — never silent") is unimplementable
without it, and it costs exactly one row.

The asymmetry is real and defensible: the TUI can afford to know the exact total because
it already paid for every row; `hsql` deliberately hasn't, so it reports `500 of >500`.
Under `-l 0` there's no `set_limit` at all, so the count is exact and `--stats` reports
`"truncated": false` against a real number.

**No adapter-interface change for limits in M1.** The clean long-term answer is for the
cursor to say whether more rows exist — a `has_more()`, or a `set_limit()` that reports
back — instead of us inferring it from an extra row. That's a fifteen-adapter rollout for
a problem the existing API already solves, so it waits. `set_limit()` and the backend's
`max_rows` are both existing features, used as they are; the only thing `hsql` does
differently is pass `limit + 1`. If a later milestone adds a real capability, `RowLimit`
is the single place that changes.

**Upstream first.** The two `textual-fastdatatable` changes in §3 are a prerequisite, and
both are non-breaking: the lazy `__init__` keeps `from textual_fastdatatable import DataTable`
working via PEP 562 `__getattr__`, and deferring `pyarrow.parquet` is invisible to
callers. Making `textual` an optional extra is a bigger change — it would let a future
lean `hsql` install skip Textual entirely, but it's a dependency break for existing
users and buys M1 nothing, since `harlequin` needs Textual regardless. Deferred.

The TUI's two workers become thin wrappers: `_execute_query` calls `execute()`,
`_fetch_data` calls `fetch()`, both still post the same messages, and the DataTable gets
the same backend object it builds today.

### 3.2 `harlequin.statements` — one grammar, one query, two slicers

Driving tree-sitter directly (§1.4) is better than every alternative I costed: 28ms
against sqlfmt's 85ms, no new SQL scanner to maintain, and — the part that matters —
**it is the same grammar and the same query the TUI already uses**, so `hsql -f script.sql`
and the editor cannot disagree about where a statement ends.

```python
def find_separators(
    text: str,
) -> list[Point]: ...  # (row, character column) past each ";"
def split(text: str) -> list[Statement]: ...  # separators -> trimmed statements
```

**`tree-sitter` and `tree-sitter-sql` become explicit, required dependencies of
`harlequin`** (Ted's call). Both are installed for everyone today, transitively via
`textual[syntax]`; depending on them directly costs 28ms, makes the guarantee real
instead of incidental, and collapses this section from "two splitters, carefully kept in
sync" to one. The accepted cost is that Harlequin stops installing on a platform with no
tree-sitter wheel — which is why `textual` made it an extra, and which the
`harlequin.sh/docs/troubleshooting/tree-sitter` page currently exists to explain. That
page becomes obsolete; PR 7 removes it.

**With that, there is exactly one splitter.** Tree-sitter is used in precisely one place
in Harlequin today — `code_editor.py`, for the semicolon query, and nowhere else
(highlighting is textual-textarea's own business and unaffected). So the editor calls
`find_separators(self.text)` and slices as it does now, and we delete `SEMICOLON_QUERY`,
`_semicolon_query`, `prepare_query`, `query_syntax_tree`, `has_shown_tree_sitter_error`,
and the whole `is_syntax_aware` fallback branch.

The one thing given up is textual-textarea's incremental tree: the shared library
reparses. Measured, that's 0.6ms for a realistic buffer and 22ms for a pathological
2000-statement one, and `selected_queries()` runs on submit rather than on keystroke. A
worthwhile trade for deleting a divergence.

**This fixes a live unicode bug in the TUI, which is why the shared library returns
character columns.** Tree-sitter's `Point.column` is a **byte** offset; Textual's own
docstring says so, and it hands the raw nodes back. But `get_text_range()` takes
*character* columns, and `code_editor.py:159` passes one straight into the other. Any
non-ASCII before a semicolon on the same line shifts the cut:

```
select '日本語';select 2
  splits today  -> "select '日本語';select"  |  "2"
  should be     -> "select '日本語';"        |  "select 2"
```

Both halves are then syntax errors. It's narrow — it needs non-ASCII before the
semicolon and a non-space after it, so `.strip()` usually absorbs the off-by-one — but
it's real, reproducible, and in `main` right now. Owning the byte→character conversion
in one place, once, is what stops `hsql` from inheriting it. Worth its own issue and a
changelog line, since it's a TUI bug fix that happens to fall out of this work.

*One known bug we don't fix:* `create function f() … $$ … ; … $$ …` splits inside the
dollar-quoted body. The TUI has it today, identically, because it's the grammar's. Its
own issue, not M1 scope — and a decent demonstration that sharing the grammar means
`hsql` inherits the TUI's behavior including its bugs, which is the definition of no
drift.

### 3.3 Output — duckdb serializes, `hsql` lays out

```python
**`hsql` writes no value-serialization code at all. duckdb is the serializer; `hsql`
only does layout.** That is the whole of §3.3, and everything below follows from it.

The temptation is to reach for the stdlib `csv` module, and on plain strings it is
indistinguishable — I diffed duckdb's writer against `csv.writer` on embedded commas,
embedded quotes, embedded newlines, nulls and unicode and got byte-identical output.
The divergence isn't escaping, it's **types**:

| value | `export.py` (duckdb) | stdlib `csv` + `str()` |
| --- | --- | --- |
| `timestamptz` | `2024-03-01 12:30:00+00` | `2024-03-01 12:30:00+00:00` |
| `boolean` | `true` | `True` |
| `blob` | `\x00\x01\xFF` | `b'\x00\x01\xff'` |

The third row is the argument. `b'\x00\x01\xff'` is a Python repr in a data file. A
hand-rolled writer doesn't just have to get RFC 4180 right, it has to own a rendering for
every type any of fifteen adapters can return — dates, intervals, decimals, blobs, lists,
structs, maps — and it will get them subtly wrong in ways nobody notices until an agent
parses one.

So the format table becomes:

| Format | Serialized by | Via |
| --- | --- | --- |
| `csv`, `tsv` | duckdb | `relation.write_csv(sep=…)` |
| `json`, `jsonl`/`ndjson` | duckdb | `COPY … (FORMAT JSON[, ARRAY TRUE])` |
| `parquet`, `orc`, `arrow`/`feather` | duckdb / pyarrow | the existing exporters |
| `table`, `markdown`/`md`, `vertical` | duckdb, then `hsql` lays it out | `CAST(col AS VARCHAR)` |
| `none` | — | rows discarded, status only |

`write_csv` already takes `sep`, `na_rep`, `header`, `quotechar`, `escapechar`, `quoting`,
`encoding` — so `tsv` is `sep="\t"`, `--no-header` is `header=False`, and
`--null-string` is `na_rep`. Nothing new needed. duckdb's JSON writer emits unquoted
numbers, bare `true`/`false`, real `null`, and preserves nested structs — the array-of-row-objects
shape §5 of the product plan asks for, with `ARRAY TRUE`, and jsonl without it.

**`ResultSet.text_columns()` is `CAST(col AS VARCHAR)`, and it's what keeps `-F table`
honest.** The human-facing layouts need strings, and if they derived them any other way
they would disagree with `-F csv` about what a timestamp or a blob looks like. Casting
through duckdb returns `1234.5600`, `true`, `\x00\xFF` — the same text `write_csv`
produces — with SQL `NULL` arriving as Python `None`, so it stays distinguishable from
the literal string `"NULL"` and `--null-string` works properly. `hsql` then does padding,
pipes and alignment, and nothing else.

**One serializer across every adapter is a feature, not a side effect.** Routing a
Postgres result through duckdb means Postgres, BigQuery and DuckDB timestamps print
identically — which is story A2 in the product plan ("the flags, output shape, and exit
codes are identical, so I don't relearn a CLI per database"). Getting that from a shared
serializer rather than from discipline is the cheap way to get it.

*Robustness:* everything reaches duckdb via `duckdb.from_arrow()`, so an arrow type
duckdb can't ingest would break the path. I tested large_string, dictionary-encoded,
duration, map and fixed-size-binary — all fine. There's still a `str()`-based fallback
with a stderr warning, as belt and braces rather than an expected path.

**`hsql` never calls `cell_formatter`, and this is the one place we deliberately don't
reuse the TUI's code.** `textual_fastdatatable.format.cell_formatter` is *display*
formatting: floats and ints as `f"{obj:n}"` (locale-aware thousands separators), booleans
as `✓ True`, strings as Rich markup. Correct for a data table a human is reading;
catastrophic in a CSV. Display and serialization are different operations, and conflating
them is how `1,234,567` ends up in a numeric column.

The corollary is a live trap: **`hsql` must not call `set_locale()`.** The `harlequin` CLI
does (`cli.py:339`), and inheriting it would make output vary with `LC_ALL` — a direct
violation of principle 4's "identical output" promise, visible only on someone else's
machine. `CAST AS VARCHAR` is locale-independent; `f"{obj:n}"` is not.

Column widths for `-F table` are measured over the strings from `text_columns()`, not
taken from `backend.column_content_widths` — the latter measures the width of the
*displayed* form we're not displaying.

They are measured in **terminal cells**, with `wcwidth` (Ted's call). An earlier draft
of this section said `max(len(s))` and dismissed display width as a great deal of
surface for a cosmetic problem; that was wrong on the cost, because `wcwidth` is already
installed and is one function call. It's the difference between a table that lines up
and one that doesn't for anyone whose data isn't Latin. `wcwidth` arrives transitively
via `prompt_toolkit` today — one of the packages the headless import graph exists to
keep *out* — so like `tree-sitter` in PR 2 it becomes a direct dependency. An ASCII fast
path keeps the common cell at `len()`.

### Consequences worth stating plainly

**Nothing streams in M1, including CSV.** duckdb's writers are file-producing, so `-o PATH`
hands duckdb the path and stdout writes to a temp file and copies it out. My earlier
draft claimed csv and jsonl would stream; that was wrong twice over, because `fetchall()`
has already materialized the whole result before any writer sees it. Streaming is
uniformly an M5 problem that starts at the cursor, not at the formatter — which is
simpler than a format layer where some writers stream and some don't.

**One code path to stdout, not two.** `/dev/stdout` works on Linux and I verified it, but
it doesn't exist on Windows, and a platform-conditional output path is two paths to test
for no benefit. Temp file then `copyfileobj` everywhere. At the default `--limit 500`
it's unmeasurable; at `-l 0` duckdb writing a temp file beats building strings in Python.

**Newlines are still part of the contract.** duckdb writes `\n`; the copy to stdout must
be binary so Python's text-mode translation doesn't turn it into `\r\n` on Windows. Same
test as before, and now it covers every format at once.

**The overlap I flagged last draft is gone.** There was going to be one CSV writer for the
TUI's export dialog and another for `hsql`. There's one, and the export dialog's options
(compression, quoting, encoding) are already in its vocabulary.

### `harlequin.export` and `harlequin.layout`

Given the above, the split isn't text-versus-columnar, it's **who serializes**:

```python
# harlequin/export.py — writing a result set to a file, serialized by duckdb or pyarrow
def write_file(data: pa.Table, path: Path, format_name: str, options: Mapping[str, Any]) -> None: ...
def file_format_names() -> list[str]: ...

# harlequin/layout.py — arranging already-serialized text for a reader
@dataclass(frozen=True)
class LayoutOptions:
    header: bool = True
    footer: bool = True
    aligned: bool = True
    null_string: str | None = None
    color: bool = False

class Layout(Protocol):
    def write(self, result: ResultSet, out: TextIO) -> None: ...

def get_layout(name: str, options: LayoutOptions) -> Layout: ...
def layout_names() -> list[str]: ...
```

`export.py` keeps its name and its job, loses the `ResultsTable` coupling from §1.6, and
gains `tsv` and `jsonl` as thin option variants of formats it already writes. `layout.py`
is new and small: three layouts, no type knowledge, no duckdb.

`LayoutOptions` is what makes psql's flag algebra fall out for free: `-t` is
`header=False, footer=False`, `-A` is `aligned=False`, and `-tA` needs no special case
because it was never one — two independent options, which is how psql users already think
about them. `--no-header` and `--null-string` also reach `export.py` as `header` and
`na_rep`, so the same four flags mean the same thing in every format.

### 3.4 `harlequin.plugins` — naming an adapter without importing it

```python
def adapter_names() -> list[str]: ...  # entry-point names only
def load_adapter(name: str) -> type[HarlequinAdapter]: ...  # imports exactly one
def load_adapter_plugins() -> dict[str, type[HarlequinAdapter]]: ...  # existing
```

`adapter_names()` reads entry-point *names* without calling `ep.load()`, so it costs
nothing. That's what makes the two-phase parse in §3.6 possible, and per §1.7 it is worth
~160ms of the prototype's runtime — the difference between 380ms and 220ms.

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

An invocation only ever uses one adapter, so for *execution* this is not a compromise.

**Help is the exception, and it works differently (Ted's call).** My earlier version had
plain `hsql --help` render the default adapter's options, which is misleading: a Postgres
user reading `hsql --help` would see DuckDB's connection options and reasonably conclude
those were the only ones. `--help` is the one path that has to be true for every adapter.
So:

- **`hsql --help`** shows the adapter-agnostic surface — every option in §5 of the product
  plan, the format list, the exit codes — plus the *names* of installed adapters and a
  line saying `run 'hsql --help -a <adapter>' for its connection options`. No adapter is
  imported to render this at all, only `adapter_names()`.
- **`hsql --help -a postgres`** imports postgres alone and adds its connection options.
- **`hsql --help -P prod`** does the same for whatever adapter the profile names, which is
  the form a human will actually reach for.

This is better than what I had on every axis: it's honest, the top-level help is small and
stable (the thing an agent reads first), the adapter options are one explicitly signposted
call away, and rendering the common case imports nothing. It also degrades well as the
ecosystem grows — twenty installed adapters make the help *list* longer by twenty lines,
not the help *body* by twenty option tables.

`hsql info --json` (M2) remains the way to enumerate everything at once.

`harlequin` itself keeps its current all-adapters help. Changing it would alter published
`--help` output and the docs built from it, for no benefit to a TUI already paying for
Textual.

`diagnostics.py` owns the stderr contract:

```python
class ExitCode(IntEnum):
    OK = 0; QUERY = 1; USAGE = 2; CONNECTION = 3; TIMEOUT = 4; INTERRUPT = 130

def exit_code_for(error: BaseException) -> ExitCode: ...
def report_error(error: BaseException) -> None:          # "hsql: error: ..."
def report_truncation(result: ResultSet, limit: RowLimit) -> None: ...
def report_stats(...) -> None:                            # one line of JSON
```

Two things worth stating because they're easy to get wrong:

- **The truncation notice fires under `-t`.** `-t` suppresses stdout chrome; it does not
  suppress warnings. A flag that silently defeated truncation reporting would undo the
  principle it's meant to coexist with.
- **Exit codes are hsql's contract, not Harlequin's**, so the mapping lives here rather
  than in `harlequin.exception`. `HarlequinQueryError → 1`, `HarlequinConnectionError → 3`,
  `HarlequinConfigError → 2`, `KeyboardInterrupt → 130`.

---

## 4. Sequencing

One upstream release, then two Harlequin releases. The first Harlequin release contains
no new user-facing surface at all; the second is the feature. Every PR in the first is
independently reviewable, independently testable, and behavior-neutral for the TUI.

### Upstream — `textual-fastdatatable` (0.17)

**PR 0.** Lazy `DataTable` in `__init__.py` via PEP 562; `pyarrow.parquet` deferred into
`ArrowBackend.from_parquet`. Add a test asserting `"textual" not in sys.modules` after
importing `textual_fastdatatable.backend` — otherwise the next contributor re-adds a
convenience import at the top of `__init__` and nobody notices for a year. Both changes
are non-breaking; harlequin then bumps its pin from `==0.16.0` to `==0.17.0`.

Blocks PR 2 and PR 3. Doesn't block PR 1, so it can be running in parallel.

### Release A — the plumbing (2.9)

**PR 1 — Import hygiene, and the guard that keeps it.**
Lazy `harlequin/__init__.py` via PEP 562, preserving every current name.
`AutoBackendType` under `TYPE_CHECKING` in `adapter.py`, `harlequin_duckdb`,
`harlequin_sqlite`. `NewCatalog`/`NewCatalogItems` move to `harlequin/messages.py`, with
a module-level `__getattr__` shim in `catalog.py` so existing imports keep working
without importing Textual. Deferred renderer imports in `options.py`; `_CustomValidator`
moves to `copy_widgets.py`, which is already the option-widgets library. Fix the two
stdout leaks from §1.5. Add import-linter contracts and the cold-start benchmark script.

Ships value on its own: importing `harlequin_duckdb` goes from 691ms to 56ms, which
every adapter's test suite and every library consumer feels.

**PR 2 — `harlequin.statements` and `harlequin.query`.** Add `tree-sitter` and
`tree-sitter-sql` to `dependencies`. Build the splitter; refactor `CodeEditor` onto it and
delete its fallback branch, fixing the unicode column bug in the same move; land the
tricky-SQL corpus. Then the execution core, with `_execute_query` and `_fetch_data`
refactored onto it. Delete the dead `_split_query_text`. Snapshot tests are the safety
net for the TUI refactor.

This is the only PR in Release A with user-visible behavior in it — the unicode fix — so
it gets a changelog entry and its own issue while the rest stay silent.

**PR 3 — `export.py` decoupled from `ResultsTable`, plus `harlequin.layout`.** Extend
`export.py` with `tsv` and `jsonl` (option variants of formats it already writes) and the
temp-file-to-stream path; add the three layouts and `ResultSet.text_columns()`. Golden
files per format. No user-visible change; neither has a consumer until PR 5.

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
README, changelog. Retire the `troubleshooting/tree-sitter` page, which describes a
degraded mode that no longer exists (§3.2).

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
  not contain `textual` or `questionary`. Plus subprocess tests asserting
  `"textual" not in sys.modules` after importing the headless core, and the same test
  upstream after importing `textual_fastdatatable.backend`. These fail identically on
  every machine.
- *Informational:* the benchmark reports `hsql -c "select 1"` against DuckDB into the
  job summary, failing only past a loose ceiling (600ms) that catches a real regression
  without catching a busy runner.

The import contracts are load-bearing. Timing is downstream of them.

Beyond that:

- **`CliRunner` tests** for `hsql`, following the existing `test_cli.py` patterns and its
  adapter-mocking fixtures.
- **Golden files** per format (syrupy snapshots, as everywhere else in this repo)
  against a fixture result set with nulls, unicode, wide
  values, duplicate column names, and zero rows. Zero rows is the case that separates
  "the query returned nothing" from "the query failed" — A3 in the product plan — and
  the one most likely to be wrong in a format nobody exercised.
- **A type-coverage fixture** — timestamp, timestamptz, date, decimal, float, boolean,
  blob, list, struct, map, and null in each — rendered into every format, as golden
  files. This is the table in §3.3 turned into a test: it's what would catch a change in
  duckdb's rendering, and it's the reason `-F table` and `-F csv` can be asserted to
  agree cell for cell.
- **`text_columns()` agreement:** for that fixture, the strings `-F table` prints are the
  same strings `-F csv` writes.
- **stdout purity:** for each format, stdout bytes identical with and without `--stats`;
  a query error leaves stdout completely empty.
- **Determinism:** identical bytes whether stdout is a pipe, a file, or a pty; `\n`
  regardless of platform, which now means the temp-file copy is binary; **identical bytes
  under `LC_ALL=de_DE.UTF-8`**, the regression test for §3.3's locale trap.
- **`-o PATH` and `> PATH` produce identical bytes** for every format — the one thing the
  temp-file path could plausibly get wrong.
- **`hsql -tAc "select count(*)"` returning a bare number and nothing else** as its own
  named test. It's the idiom the scripting audience will try first.
- **Truncation:** exactly-at-limit, one-over-limit, and under-limit, on both duckdb and
  sqlite — the two implement `set_limit` differently enough to matter.
- **The tricky-SQL corpus** against `statements.split()` and, through `selected_queries()`,
  the editor — same fixtures, one splitter underneath. Includes non-ASCII before a
  semicolon with no following space (the §3.2 bug, which fails on `main`) and a
  skip-marked dollar-quoting case so the known bug is recorded rather than forgotten.

---

## 6. Decisions

**Settled.**

- *Limits stay on the existing machinery.* `cursor.set_limit()` and the backend's
  `max_rows`, exactly as they are — no new adapter-interface method for truncation in M1
  (see §3.1).
- *No overflow detection in the TUI.* It doesn't need any: `--limit` there is a soft
  display cap over a full fetch, so it already reports the exact total (§3.1).
  `detect_overflow=False`, no snapshot churn in PR 2.
- *Streaming is out.* Not designed around, not partially built — see §3.3 and §7.
- *`tree-sitter` and `tree-sitter-sql` become required dependencies.* One splitter, no
  fallback, the troubleshooting page retired (§3.2). Accepted cost: no install on
  platforms without a tree-sitter wheel.
- *`--help` is adapter-agnostic*, listing adapter names and pointing at `--help -a X`
  for connection options (§3.6). Ted's design; better than what I proposed.
- *`--result` defaults to `all`, in every format* (below).

### On `--result`

The flag only matters when one invocation produces more than one result set — `hsql -c
"select 1; select 2"`, or the common `-f script.sql` shape of a few setup statements
followed by the query you actually care about. `all` emits every result set; `last` emits
only the final one; `N` picks one. With a single statement, which is most invocations,
the flag does nothing.

The product plan's proposal was `all` for text formats and `last` for data formats. The
problem isn't the values, it's that the *same command* would emit different things
depending on `-F`, which is exactly the context-dependence principle 4 exists to prevent.
Agreed on consistency, so:

**`--result all` is the default everywhere. Formats that can't hold multiple result sets
say so rather than lying.** Concretely:

- One result set: works in every format, no change.
- Multiple, into `table` / `markdown` / `vertical` / `jsonl`: all of them, in order.
- Multiple, into `csv` / `tsv` / `json`: **error, exit 2** —
  `hsql: error: 3 result sets, but csv holds one; use --result last or --result 3`.

Writing two headers into one CSV is silent corruption of the same family as silent
truncation, and an error that names the fix costs an agent one retry with a clear signal.
The alternative — silently defaulting to `last` — throws away data the user asked for
without saying so.

**Still open.** Nothing.

---

## 7. Explicitly not in M1

`--read-only`, `--timeout`, `--dry-run`, `--single-transaction` (M2 — the first three
need adapter-interface additions and an ecosystem rollout). Every subcommand: `catalog`,
`describe`, `fmt`, `spec`, `info`, `config`, `history`, `open`, `mcp`. Streaming of any
kind — it starts at the cursor interface, not the format layer (M5; see §3.3 and §8).
Unifying the TUI's export dialog onto `write_file` (mechanical, but it moves a UI).
Making `textual` an optional extra of
`textual-fastdatatable`, and the separate lean `hsql` distribution it would enable (§4
of the product plan: near-reversible, indefinitely deferrable). Fixing dollar-quoted
statement splitting (its own issue, affects the TUI equally).

Bare `hsql` with no arguments prints help and never launches the TUI, from the first
release.

---

## 8. Corrections to the product plan

All of these are **applied** in `harlequin-for-agents.md` in the same PR as this
document; they're recorded here so the reasoning behind each change is written down
somewhere.

- **§4 says the full adapter option matrix on `hsql --help` is "unavoidable and shared."**
  It's avoidable. `hsql --help` lists adapter *names* and points at `--help -a X`, which
  is both a token saving and the fix for the only unbounded startup cost (§3.6).
- **§12 lists cold start as a risk with "may need lazy entry-point resolution."** Not a
  maybe: it's worth 160ms of a 380ms invocation with four adapters installed, and it
  grows without bound. A requirement of M1, not a contingency.
- **§12's cold-start risk should name `textual-fastdatatable`, not just "Textual creeping
  in."** The 265ms the adapter interface pays today is upstream of this repo, and an
  import-linter rule here would never have caught it.
- **§5's truncation guarantee needs the `limit + 1` fetch to be implementable at all.**
  Worth stating in the product plan, because it's exactly the kind of thing that gets
  designed out as an implementation detail and then quietly breaks the promise.
- **§5 says "Large results stream" and lists it as an M1 concern.** Nothing streams in
  M1, and it can't: `HarlequinCursor.fetchall()` materializes the whole result before any
  writer exists, so the format layer is downstream of the problem. Streaming is an M5
  change to the cursor interface, and M1 doesn't design around it — `rows()` returns an
  iterator because that's the natural shape, not as a hedge. Agreed with Ted; the product
  plan's §5 bullet should move to M5 outright.
- **§5's truncation footer says `… 500 of N rows`, and N is unknowable.** Under a hard
  fetch limit we deliberately never learn the true total — that's the point of the limit.
  The footer is `500 of >500` -- strictly greater, which the limit+1 fetch proves and
  the conventional `500+` would understate -- and only `-l 0` yields an exact count. (The TUI *can* say
  `100,000 of 3,412,887` because its limit is a display cap over a full fetch; see §3.1.)
- **§5 lists `--limit` as though it means the same thing in both commands.** It doesn't:
  soft display cap in the TUI, hard fetch limit in `hsql`. Worth one explicit sentence in
  the docs rather than letting a reader assume they match.
- **§5's `--result` default is format-dependent** (`all` for text, `last` for data), which
  makes the same command emit different things depending on `-F`. It's `all` everywhere,
  with an error rather than silent corruption where a format can't hold more than one
  result set (§6).
