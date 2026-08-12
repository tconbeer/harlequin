# M2 Technical Plan — self-description and safety

Implementation plan for milestone M2 of [Harlequin for agents](./harlequin-for-agents.md),
following [the M1 technical plan](./m1-hsql-technical-plan.md). The product plan says what
`hsql` is for; M1 built the command and the execution core under it. This one is about the
verbs that let an agent write a correct query before it runs one, and the three flags that
let a human hand an agent a warehouse credential.

**M2's scope, from the roadmap:** `catalog` (one level below the match, child counts, node
budget), `describe`, `info --json`, `spec --json`, `fmt`, `config validate/show/schema/init`,
capability flags, the secret option type and declarative redaction
([#667](https://github.com/tconbeer/harlequin/issues/667)), env interpolation
([#898](https://github.com/tconbeer/harlequin/issues/898)), `--read-only`, `--timeout`,
`--dry-run`, and a published JSON Schema. Stretch: `find` +
`implements_catalog_search`, optional `fetch_descendants`. `--single-transaction` is here
too — M1 §7 deferred it to M2 even though the roadmap's cell omits it.

**Where M1 actually got to.** PRs 1–6 shipped: the import-hygiene work, the statement
splitter and execution core, `export`/`layout`, the shared profile-merge and one-adapter
loading, `hsql` itself, and the two commands pointing at each other. PR 7 (the docs topic,
against `tconbeer/harlequin-web`) is in flight. PR 8, the agent eval suite, is not being
done — so M2 has no automated measure of "an agent used the right flag first try," and the
guards below are all deterministic ones.

**Bottom line up front.** Nothing in M2 needs a new execution path, a new output format
family, or a second config parser: catalog listings are result sets, so `--format`, `-o`,
`-t/-A` and `--stats` already apply to them. What M2 does need is **five additive fields on
the adapter contract**, because four of its headline features are unimplementable without
them — the catalog knows a column is `##` but not that it is `DECIMAL(18,2)`, no adapter can
say whether it enforces read-only, and a cancelled DuckDB query today comes back as an empty
result set with exit code 0. Those are measured, below, not feared.

---

## 1. Where the code actually is today

Measured on this checkout — 2.8.1 plus the unreleased M1 work — on Python 3.10.15, Linux,
with four adapters installed (duckdb, sqlite, postgres 1.3.1, mysql 1.3.0). Wall times are
the best of five runs of the real console script.

```
hsql -c "select 1"      257ms
hsql --help              90ms
hsql --version           87ms
harlequin --version     767ms
```

M1's targets held. Everything M2 adds has to keep them, which is most of why §3 is shaped
the way it is.

### 1.1 The catalog is lazy in the right way, and resolving a path is itself a walk

`get_catalog()` returns the top level only — one query — and every level below it is a
separate `fetch_children()` call on the item you are standing on. That is exactly the shape
the product plan asks for, and it is why "one level below the match" is cheap.

But there is no way to *reach* an item except by fetching its ancestors. To call
`fetch_children()` on `mydb.analytics.orders` you need that item object, which only
`SchemaCatalogItem.fetch_children()` can hand you, which needs the schema item, which needs
the database item. I built a DuckDB file with 400 relations in one schema and counted the
round trips:

| Call | Children | Round trips | Local ms |
| --- | --- | --- | --- |
| `hsql catalog` | 1 database | 1 | 1.9 |
| `hsql catalog wide` | 2 schemas | 2 | 10.2 |
| `hsql catalog wide.analytics` | 400 relations | 3 | 12.8 |
| `hsql catalog wide.analytics.t000` | 3 columns | 4 | 22.8 |
| `hsql catalog wide.analytics --depth 2` | 400 relations + 1200 columns | **403** | **2303** |

So the cost of a listing is **one round trip per path segment, plus one** — not the "exactly
one `fetch_children()`" the product plan's §6 claims. On local DuckDB the difference is
20ms; on BigQuery or Trino it is four network calls where the plan promised one, and it is
worth stating because it is the number that decides whether `describe` is one call or four.

The last row is the sharp edge, and it is sharper than §12 of the product plan guessed:
`--depth 2` on a wide schema is 403 round trips and 2.3 seconds **against a local
file**. It is also the single most useful catalog call an agent can make. Child counts and
a node budget are what keep that honest, and they are not optional garnish.

### 1.2 Catalog depth is adapter-specific, and nothing declares it

DuckDB and Postgres are four levels (database → schema → relation → column). SQLite is
three (database → relation → column). BigQuery's own hierarchy is project → dataset →
table, which is four levels wearing different words. Nothing in `harlequin.catalog` says
how deep a catalog goes or what a level is called.

So `hsql catalog` cannot document its path grammar as `database.schema.table`. Paths are
**positional segments** whose meaning is the adapter's, and the type label on each item
(`db`, `sch`, `t`, `v`) is what tells a reader what they got. That is a small design
constraint with a large documentation consequence, and it is better than inventing a
level vocabulary that would be wrong for a third of the ecosystem.

### 1.3 The catalog knows the short type, and throws the real one away

`CatalogItem.type_label` is documented as "a short (1-3 chars) label" — `##` for a bigint,
`s` for a varchar, `ts` for a timestamp. It is a Data Catalog affordance: it has to fit in a
tree column next to the name.

The product plan's compact format is `analytics.orders(id BIGINT, customer_id BIGINT, total
DECIMAL(18,2))`. **The catalog contract cannot produce that string.** `hsql catalog
mydb.analytics.orders --format compact` would render `id ##, customer_id ##, total #.#`,
which tells an agent almost nothing and would send it back to `select * limit 0` to find
out what it is joining on.

The data is right there and discarded. `DuckDbConnection._get_columns()` selects
`column_name, data_type` from `information_schema.columns` and then does
`type_label=self.connection._short_column_type(column_type)`, dropping `column_type` on the
floor. SQLite's is the same shape. So this is a field on `CatalogItem`, not a new query
(§3.2).

Neither is there anywhere to hang a child count. `400 relations` next to a schema is what
lets an agent decide *not* to recurse — the whole mitigation for §1.1 — and there is no
field for it.

### 1.4 Nothing can say what an adapter can do without importing it, and importing it isn't enough

Today's capability surface is three things on the adapter class: `IMPLEMENTS_CANCEL`,
`implements_copy` (derived from `COPY_FORMATS`, and deprecated), and `provides_details`.
Everything else — `cancel`, `validate_sql`, `copy`, `transaction_mode` — lives on the
*connection*, and the connection class is not reachable from the adapter class without
connecting to a database.

Detecting an override reflectively (`type(conn).validate_sql is not
HarlequinConnection.validate_sql`) is unsound, and the counter-example ships in this repo:
`HarlequinSqliteConnection.validate_sql` **is** defined, and its body is `raise
NotImplementedError`. A reflective probe would report SQLite as validating SQL, and
`--dry-run` would then fail at the point of use rather than at the point of decision.

Cost, measured, with those four adapters installed:

| | ms | modules |
| --- | --- | --- |
| entry-point *names* (`adapter_names()`) | 46 | 126 |
| names + dist name and version (`ep.dist`) | 51 | 128 |
| `load_adapter("duckdb")` | 114 | 187 |
| `load_adapter_plugins()` — all four | 309 | 425 |

Two things follow. Adapter **versions are free** — `ep.dist.version` costs 5ms over reading
names, so what is installed is answerable without importing any of it. (Capability flags are
class variables, so reporting *those* does cost the import; §3.4.) And anything that wants
every adapter's *options* pays 300ms and rising, which is fine for `spec --json`, a
once-per-task lookup, and not fine for anything on the query path.

### 1.5 Options can be declared but not described, and nothing marks a secret

`AbstractOption` renders three ways — `to_click()`, `to_widgets()`, `to_questionary()` — and
has no fourth. There is no serialization, so `hsql spec --json` has nothing to call, and the
debug-info screen builds a markdown table by reaching for `opt.name`, `opt.short_decls` and
`getattr(opt, "default", None)` — duck-typing at a distance, which is what a real
`to_dict()` would replace.

Adding an **abstract** method is not available: `AbstractOption` is public API and
third-party adapters subclass it. Anything added here has to be concrete, with a base
implementation that works for a subclass that has never heard of it.

Nothing declares a value sensitive. Sampling the ecosystem, `harlequin-postgres` 1.3.1
declares twelve options, of which `password` is a plain `TextOption`, `sslkey` is a
`TextOption`, and `passfile` is a `PathOption`. So `hsql spec --json` would today teach an
agent that `--password` exists and say nothing about why it must not be typed on a command
line, where `ps` and shell history can read it.

### 1.6 Config is merged shallowly, carries no provenance, and interpolates nothing

`_merge_config_files()` is a top-level `dict.update()` per file. Which means:

```
~/.harlequin.toml     [profiles.alpha]
./.harlequin.toml     [profiles.beta]
merged                {'profiles': {'beta': {...}}}
```

**A cwd config file that defines any profile hides every profile in the home file.**
Measured, above. "Later wins" is true per top-level key, not per profile, which is not what
a reader of the docs expects and not what `hsql config show --provenance` could usefully
report — provenance per top-level key would be three lines and no help.

Reading is `tomllib` (fast, and every start-up pays it, including on the `pyproject.toml`
that sits in any Python project's cwd) and writing is `tomlkit` (slow, preserves comments,
loaded only when something writes). That split is right and M2 keeps it — but note that
`config_wizard` reads `ConfigFile.relevant_config` and writes it back through `update()`, so
**anything that transforms values on read will be written back transformed** unless the read
path used for writing stays raw. That is the whole implementation risk in env interpolation
(§3.5).

Schema errors (`_raise_on_bad_schema`) are good prose with no machine channel and no
positions: `tomllib` reports a line for a *parse* error inside its message, and neither
parser will tell you where key `profiles.prod.limitt` sits in the file.

### 1.7 sqlfmt is a dependency, and the headless path is contractually forbidden to import it

`shandy-sqlfmt` is already required, and `components/code_editor.py` imports it at module
scope for the editor's format action. The `hsql does not reach the TUI` import-linter
contract lists `sqlfmt` among the forbidden modules, and
`tests/unit_tests/test_import_hygiene.py` asserts it stays out of `sys.modules` after every
headless import.

That is correct and worth keeping: importing sqlfmt costs **196ms and 281 modules**, and
217ms on top of `harlequin.hsql.cli` (which alone is 107ms/187). `hsql fmt` is the one verb
that must pay it, and no other invocation may. Same shape for the two other expensive
things a verb needs: `questionary` (156ms, and `hsql` may never prompt anyway) and `tomlkit`
(49ms, +8ms on top of the CLI, needed only by `config init`).

### 1.8 There is nothing to build `--read-only` on, and `--single-transaction` is thinner than it looks

No adapter declares read-only as a capability. DuckDB and SQLite each declare their own
`read-only` *option*, which core could set by name — but `harlequin-postgres` declares no
such option at all, so a convention-based `--read-only` would silently do nothing on the
adapter most likely to be pointed at production. (Postgres can enforce it; it wants `set
default_transaction_read_only = on` at connect, which is adapter work, not core work.)

`--single-transaction` wants `HarlequinConnection.transaction_mode`, which returns
`HarlequinTransactionMode(label, commit, rollback)` or None. SQLite declares Auto/Manual on
3.12+; Postgres declares Auto/Manual; **DuckDB declares none** — `DuckDbConnection` never
overrides the property, so it returns None. So the flag would refuse on the flagship
adapter unless the in-tree DuckDB adapter grows a transaction mode, which it should
(§3.6).

### 1.9 A cancelled query returns zero rows and exit code 0

This is the one that would have shipped as a bug. I ran a billion-row DuckDB aggregate
through `harlequin.query` on a worker thread and called `connection.cancel()` from the main
thread:

```
cancel() returned in 0.0ms
executed: [(has_result_set=True, error=None)]
rows: 0
elapsed: 702ms
```

`DuckDbCursor.fetchall()` catches `duckdb.InterruptException` and returns `None`; `fetch()`
turns `None` into an empty backend, correctly, because that is also what a real empty result
looks like. So a naive `--timeout` prints an empty result set, writes no diagnostic, and
exits 0 — reporting "your query returned nothing" for a query that was killed. That is the
silent-wrong-answer family the whole product plan exists to avoid, and no amount of care in
the adapter helps, because the adapter cannot tell an interrupt it was asked for from one it
wasn't.

Two more things I measured while establishing what a deadline can do:

- **Exiting while a worker thread is inside DuckDB aborts the process.** `sys.exit(4)` with
  a live daemon thread in `to_arrow_table()` produces `terminate called without an active
  exception` and exit code **134**, which is not in the documented table. `os._exit(4)`
  after an explicit flush exits 4 with stdout intact.
- So a deadline is implementable, but only as: cancel → wait → flush → `os._exit`. And with
  an adapter that cannot cancel, there is no way to stop the work at all.

### 1.10 `--dry-run` on DuckDB catches syntax errors and nothing else

`DuckDbConnection.validate_sql()` runs `json_serialize_sql` and deliberately treats any
non-parser error as valid, because DDL comes back as "not implemented". Measured:

| statement | `validate_sql` |
| --- | --- |
| `select 1` | valid |
| `selct 1` | **invalid** |
| `select * from no_such_table` | valid |
| `insert into no_such_table values (1)` | valid |
| `create table t (a int)` | valid |

A missing table validates. For an agent, "does this query reference things that exist" is
most of what a dry run is for, so `--dry-run` built on this alone would over-promise.

There is a better check for the half of the language that supports it: `PREPARE <stmt>`
binds without executing. Measured on DuckDB — `prepare _p as select * from nope` raises
`CatalogException: Table with name nope does not exist!`, `insert into t values (1)`
prepares fine and inserts nothing, and DDL (`create`, `drop`) is a parser error because
`PREPARE` doesn't take it. So it is an *additional* check for statements that parse and
aren't DDL, not a replacement. That is in-tree adapter work (§3.6), and third-party adapters
keep whatever `validate_sql` they have.

---

## 2. The obstacles, stated plainly

1. **The adapter contract can't answer four of M2's questions.** What is this column's real
   type; how many children does this node have; can you enforce read-only; can you validate
   SQL. All four are additive fields with defaults, and all four need an ecosystem rollout
   before they are true everywhere.
2. **Resolution is a walk, and depth is unbounded cost.** One round trip per path segment,
   403 for one `--depth 2`. The defaults have to be safe by construction and the cost has to
   be visible before the call, not after it.
3. **`hsql` has one verb and needs six, without losing a 90ms `--help`.** (Seven with the
   stretch `find`.) The two-phase parse M1 built exists to avoid importing adapters; the
   verbs add a third question — *which verb* — that has to be answered before either of the
   other two, and three of them need imports the query path must never pay: sqlfmt,
   tomlkit, and every installed adapter at once.
4. **A timeout cannot trust the adapter to report itself.** §1.9. `hsql` has to attribute
   the cancellation it caused, because the adapter can't.
5. **Secrets leak through four new mouths at once.** `info`, `spec`, `config show` and
   errors are all output surfaces M2 adds, and every one of them handles connection
   options. Redaction has to be a property of the declaration, not a rule applied four
   times.

Note what is *not* on the list. There is no new output subsystem: catalog listings are rows,
so `--format`, `-o`, `-t`, `-A`, `--display-rows` and `--stats` are already written and
already tested (§3.3). And there is no new config parser: provenance, interpolation and
schema generation are all things to do to the config we already read.

---

## 3. Target architecture

```
harlequin/
  adapter.py      MOD  + IMPLEMENTS_READ_ONLY, IMPLEMENTS_VALIDATE_SQL, capability reporting
  catalog.py      MOD  + CatalogItem.type_name, CatalogItem.child_count
  navigate.py     NEW  locating and listing catalog items by path, under a budget
  options.py      MOD  + secret=..., + to_dict()
  config.py       MOD  per-key merge, provenance, ${VAR} interpolation
  config_schema.py NEW the JSON Schema for a config file, generated from what is installed
  redact.py       NEW  what must never be printed, in one place
  query.py        MOD  text_columns() returns string data unchanged
  layout.py       MOD  + compact, tree
  hsql/
    cli.py        MOD  three-phase parse: verb, then adapter, then the command
    verbs.py      NEW  the verb table -- names and help lines, importing nothing
    deadline.py   NEW  a wall clock over a run, and how it exits
    commands/     NEW  catalog.py describe.py find.py info.py spec.py fmt.py config.py
```

M1's rule stands and now covers eight verbs rather than one:

> **stdout is written only by `harlequin.layout` and `harlequin.export`. stderr is written
> only by `harlequin.hsql.diagnostics`.**

M2 adds a second, which is the whole milestone in a sentence and is also testable:

> **No verb pays for what it did not ask for.** `--help` imports no adapter; `spec` and
> `config` import no database driver; `info` opens no connection; `catalog` fetches one
> level; and the query path imports neither sqlfmt, tomlkit nor questionary.

### 3.1 `hsql` grows verbs, and the collision rule is written down

The command stays a single `click.Command` per invocation rather than becoming a
`click.Group`, because the run form has to keep its positional `CONN_STR` and its flags at
the top level — `hsql my.db -c "select 1"` is the shape M1 shipped and the shape psql
muscle memory expects. `build_cli(argv)` already inspects the raw arguments to decide which
adapter's options to attach; it gains one question ahead of that:

```python
# harlequin/hsql/verbs.py
VERBS: dict[str, tuple[str, str]] = {          # name -> (module, one-line help)
    "catalog":  ("harlequin.hsql.commands.catalog",  "List catalog objects at a path."),
    "describe": ("harlequin.hsql.commands.describe", "Describe one object in full."),
    ...
}
def verb_names() -> list[str]: ...   # imports nothing
```

That is `adapter_names()` again, for the same reason: `hsql --help` renders the run form's
options plus a `Commands:` block built from this table, and imports nothing to do it. The
90ms stays 90ms.

**The collision rule.** The first argument that is not an option or an option's value is a
verb *if it exactly matches a name in the table*; otherwise it is a connection string. A
DuckDB file named `catalog` in the working directory is therefore addressed as
`./catalog`, and that sentence belongs in the docs next to the flag list. This is the
ambiguity §4 of the product plan rejected for `harlequin` — the difference is that here the
fallback is a well-defined command with required flags rather than a full-screen app, and
that the collision set is seven short words rather than every subcommand a TUI might grow.

Which verbs need what, because this is the table that keeps §3's second rule true:

| verb | adapter import | connection | notable import |
| --- | --- | --- | --- |
| (run) | one | yes | — |
| `catalog`, `describe`, `find` | one | yes | — |
| `info` | all, for capability flags; one under `-a` | **no** | — |
| `spec` | all, or one under `-a` | no | — |
| `config show/validate/schema` | all, for adapter options | no | — |
| `config init` | one | no | tomlkit |
| `fmt` | none | no | sqlfmt |

`info` not connecting is a design decision, not an omission: the diagnostic an agent runs
when the database is unreachable must not itself require the database. Capability flags
come from declarations (§3.4) precisely so that this holds — and reading a declaration
costs an import of the adapter class, but never a connection.

### 3.2 `harlequin.navigate` — paths, one level, and a budget

```python
@dataclass(frozen=True)
class CatalogPath:
    segments: tuple[str, ...]           # positional; the adapter names the levels
    glob: str | None = None             # a trailing wildcard only

@dataclass(frozen=True)
class Budget:
    max_nodes: int = 2000
    max_depth: int = 1                  # counted from the match, never from the root

@dataclass
class Listing:
    items: list[CatalogItem]            # each carries its own path
    round_trips: int
    stopped_at: CatalogPath | None      # set when the budget bit, and named in the notice

def resolve(connection, path) -> tuple[CatalogItem | None, int]: ...
def list_children(connection, path, budget) -> Listing: ...
```

Four things this module owns, and nothing else does:

**Depth counts from the match.** `--depth 1` is one round trip per level *below where you
already are*, at every level, so there is no path on which the default degrades into a walk.
Depth ≥2 from a schema is the first N+1 and it is opt-in — which, per §1.1, is 403 round
trips on a wide schema, so it is opt-in with a number attached.

**A trailing glob is sugar; an interior one is a different verb.** `analytics.ord*` filters
one resolved parent's children in the client and stays one round trip. `*.orders` cannot be
evaluated without fetching every candidate level, so it is `find`'s job (§3.7) and never
something `catalog` does quietly.

**The budget is announced, never silent.** `--max-nodes` (2000) stops a walk, and the notice
on stderr names the path to narrow rather than just the number — the same rule as row
truncation.

**Round trips are counted and reportable.** `--stats` gains `round_trips`, so the expensive
shape of a call is visible in the same channel as everything else. This is the honesty
mechanism for §1.1: an agent that can see `"round_trips": 403` learns something an agent
that waited 40 seconds does not.

The two fields this needs on the contract:

```python
@dataclass
class CatalogItem:
    ...
    type_name: str | None = None    # the adapter's own full type: DECIMAL(18,2), TIMESTAMPTZ
    child_count: int | None = None  # None means "unknown", not "zero"
```

Both default to None, both are keyword-friendly, and both are populated in-tree from data
the adapters already fetch (§1.3). `None` meaning *unknown* rather than *zero* is
load-bearing: an agent must not read a missing count as an empty schema, and the compact
format falls back to the short `type_label` when `type_name` is absent, so an adapter that
never adopts the field degrades to today's output rather than to a blank.

*One caveat for adapter authors:* `CatalogItem` is a dataclass and its subclasses append
fields, so new base fields shift positional argument order for subclass constructors. Every
in-tree call site uses keywords (`from_parent(...)`), and the adapter template does too, but
the changelog entry should say so.

### 3.3 Catalog listings are result sets, so the output layer is already written

A listing is rows: `path`, `name`, `type`, `type_name`, `query_name`, `children`. Making it
a `ResultSet` — `create_backend()` takes a sequence of rows and the column names, with no
database involved — means `hsql catalog` inherits `--format csv|json|jsonl|markdown|table|
vertical|parquet`, `-o PATH`, `-t/-A/--no-header`, `--display-rows` and the byte-for-byte
determinism M1's snapshots already pin. The alternative is a second renderer for the same
job, which is how `--format json` and `catalog --format json` end up disagreeing about how
to spell null.

Two new layouts, both of which read the path column and neither of which knows any types:

- **`compact`** — `analytics.orders(id BIGINT, customer_id BIGINT, total DECIMAL(18,2))`,
  the token-efficient form, roughly an order of magnitude cheaper than pretty JSON.
- **`tree`** — indented, for a human reading over an agent's shoulder.

One change in `query.py` makes this cheap: **`text_columns()` returns string data
unchanged.** Today every text layout casts through duckdb, which is right for query results
(§3.3 of the M1 plan) and pointless for a listing that is already strings — and it would
import duckdb (~85ms) on a `hsql -a postgres catalog` that has no other reason to. The
short-circuit is one type check, and it is also a small win for `select` results that are
all-VARCHAR.

**`query_name`, always.** Adapters already compute the correctly quoted identifier for every
item; emitting it means the agent never guesses whether this backend wants `"Orders"` or
`` `orders` ``. It costs nothing and no other client does it.

`describe` is the same rows for one object, plus whatever the adapter can add cheaply, and
`--depth 1` from a relation *is* `describe` — the difference is that `describe` resolves one
object and says everything about it, where `catalog` lists a level.

### 3.4 Capabilities are declared, not detected

```python
class HarlequinAdapter(ABC):
    IMPLEMENTS_CANCEL = False          # exists
    IMPLEMENTS_READ_ONLY = False       # new: connect() honors read_only=True
    IMPLEMENTS_VALIDATE_SQL = False    # new: the connection's validate_sql() is real
    IMPLEMENTS_CATALOG_SEARCH = False  # new, stretch: search_catalog() (§3.7)
```

Declarations rather than reflection, because reflection is measurably wrong here: SQLite
*defines* `validate_sql` and raises `NotImplementedError` from it (§1.4). Declarations are
also what let `info` answer without connecting, which is the property that makes it useful
when the database is down.

Two consequences worth stating:

- **A flag whose capability is undeclared refuses, before connecting.** `hsql --read-only -a
  postgres` exits 2 with `postgres does not declare read-only support; see hsql info --json`
  — rather than connecting, running the query, and hoping. Refusing early is the entire
  point of the flag: `--read-only` exists so a human can hand over a credential, and a
  flag that no-ops is worse than one that is absent.
- **Core still catches `NotImplementedError` at the call site.** A declaration can be
  wrong, and the failure should be a clear error rather than a traceback.

`hsql info --json` reports, per installed adapter: name, distribution, version, declared
capabilities, and whether the adapter imported at all. Plus Harlequin's version, Python's,
the platform, the discovered config files **in precedence order**, and the active profile
with every secret redacted (§3.7).

It opens no connection, but it does import: capabilities are class variables, so reading
them means loading the adapter class. That is the one cost this verb pays — 309ms for four
installed adapters, against 51ms for names and versions alone (§1.4) — and it is the right
trade for a once-per-task lookup whose entire job is that the agent stops guessing.
`hsql info --json -a duckdb` imports one (114ms) for the common case of asking about the
adapter you are using, and an adapter that fails to import is reported with its capabilities
as `"unknown"` and the import error alongside — never as `false`, because guessing false
about `implements_read_only` is the direction that gets someone hurt.

### 3.5 Config: one merge, provenance, `${VAR}`, and a schema

**The merge becomes per-key** (§1.6). `profiles` merges per profile name, and a profile
merges per option. This is a behavior change for both commands — today a cwd file's
`profiles` table replaces the home file's outright — and it is a bug fix: nobody writes a
project-local profile in order to lose their personal ones. It gets a Bug Fixes changelog
entry and a test that pins the new semantics against both files.

Provenance falls out of doing the merge properly:

```python
@dataclass(frozen=True)
class Provenance:
    value: Any
    path: Path        # which file this key's winning value came from
    overrode: list[Path]
```

`hsql config show` prints the effective config with a `# from ~/.harlequin.toml` per key,
and `--json` emits the same as `{key: {value, from, overridden_by}}`. That is the single
best troubleshooting artifact in the plan, and it is also how a human discovers that their
project's file was hiding a profile.

**`${VAR}` and `${VAR:-default}`** (Ted's call), resolved on read, over every string value
in a config file, recursively through arrays and tables:

- A bare `{` never triggers anything, which is Ted's constraint on #898: nothing anyone has
  in a password today needs escaping. `$${` is the escape for a literal `${`.
- An unset variable with no default is a `HarlequinConfigError` naming the variable *and the
  file it appears in* — exit 2, never an empty string. A password that silently becomes `""`
  is an authentication error three layers away from its cause.
- **Resolution happens on the read path only.** `ConfigFile.relevant_config` stays raw,
  because `config_wizard` reads it and writes it back (§1.6): interpolating there would bake
  a resolved secret into the user's file on their next `harlequin --config`. This is the one
  place where getting the layering wrong writes a plaintext password to disk, so it gets its
  own test.
- Shell-command interpolation (asked for on #898) is out: it is arbitrary code execution
  driven by a file discovered from the working directory.

**`config validate [--json]`** reports `{file, key_path, message, line?}` per problem. Line
numbers only where the parser gives one — `tomllib` puts a line in its parse-error message,
and neither parser reports positions for a key we merely dislike. Saying "line unknown" is
better than a made-up number and better than not shipping the verb.

**`config schema --json`** generates a JSON Schema for `.harlequin.toml` from the config
model plus the installed adapters' options — which is why `AbstractOption.to_dict()` (§1.5,
§3.7) has to land first — so it is locally accurate: it knows this machine has
`harlequin-databricks` and what its options are. A static base schema also ships in the package, at
`harlequin/schemas/config-v1.json`, for the site to publish at
`https://harlequin.sh/schemas/config/v1.json` — that publication is a `harlequin-web` PR,
outside this repo.

**`config init --non-interactive`** writes a profile through the existing `ConfigFile`
tomlkit path, so comments and key order survive, taking `--profile NAME`, `--adapter`, and
the adapter's own options as flags. It imports no questionary — the wizard stays exactly
where it is, and each command gets the affordance right for its audience.

### 3.6 Safety: read-only, timeout, dry-run, single-transaction

**`--read-only`** sets `read_only=True` in the options handed to the adapter, and refuses
before connecting when `IMPLEMENTS_READ_ONLY` is false (§3.4). In-tree, DuckDB and SQLite
already accept the kwarg and already do the right thing with it, so they declare the flag
and are done. Out-of-tree adapters are a long tail, and `hsql info --json` is what makes the
tail legible.

**`--timeout SECONDS`** is a wall clock over the whole run — execute *and* fetch, because
DuckDB executes lazily and the work happens in `fetchall()` (§1.9):

1. Refuse at parse time if `IMPLEMENTS_CANCEL` is false. There is no way to stop the work
   otherwise, and a timeout that can only lie about having stopped it is worse than no flag.
2. Run the statements on a worker thread; the main thread waits on the deadline.
3. On expiry: `connection.cancel()`, then join for a short grace period.
4. **`hsql` attributes the cancellation itself.** The result set that comes back after a
   cancel is empty and error-free (§1.9), so it is discarded rather than printed, the
   diagnostic is `hsql: error: timed out after 30s`, and the exit code is 4 — the number
   `ExitCode.TIMEOUT` has been reserving since M1.
5. If the grace period expires too, flush both streams and `os._exit(ExitCode.TIMEOUT)`.
   Plain `sys.exit` with a live worker inside DuckDB aborts the interpreter and exits 134
   (§1.9), which would be a documented-exit-code violation caused by the flag that exists to
   make failure legible.

`SIGINT` takes the same path and exits 130, which it already does.

**`--dry-run`** validates each statement through `validate_sql()` and executes nothing;
refuses when `IMPLEMENTS_VALIDATE_SQL` is false; exits 1 if any statement is invalid, naming
which. The docs have to say what it does *not* catch, per adapter, because on DuckDB today
that is "missing tables" (§1.10). In the same release, the in-tree DuckDB adapter's
`validate_sql` gains a `PREPARE` pass for statements that parse and aren't DDL, which is
what turns `select * from no_such_table` into the error an agent wanted. No EXPLAIN: it
would mean core composing SQL, which is dialect-specific and unsafe for DDL.

**`--single-transaction`** switches the connection to its manual transaction mode, runs the
script, commits at the end, and rolls back on any error; refuses where
`transaction_mode` is None. Which today includes DuckDB (§1.8), so the in-tree DuckDB
adapter grows an Auto/Manual mode in the same release — a small change that also gives the
IDE its transaction button on DuckDB.

### 3.7 `harlequin.redact` — one place, four mouths

```python
def redact_profile(profile: Profile, options: Sequence[AbstractOption]) -> Profile: ...
def redact_conn_str(conn_str: Sequence[str]) -> list[str]: ...
def redact_text(text: str, secrets: Container[str]) -> str: ...
```

Driven by a declaration, not a name list: `AbstractOption` gains `secret: bool = False`, as
a class attribute *and* a keyword, so a third-party subclass that has never passed it still
answers False rather than raising. `to_dict()` reports it, which is what teaches an agent
not to construct `hsql --password hunter2` in the first place; `to_questionary()` masks the
input; and `info`, `spec`, `config show` and error rendering all route through
`redact_profile`.

Two things the declaration cannot cover, both worth their own handling:

- **Connection strings are positional**, so no option type describes
  `postgres://user:pw@host/db`. `redact_conn_str` is DSN-aware — and
  [#354](https://github.com/tconbeer/harlequin/issues/354) is evidence people really do put
  passwords there.
- **A driver exception that echoes a DSN** does not pass through our option layer, so
  `redact_text` is the backstop applied to error messages before they are printed.

**`--password-stdin`** reads one line from stdin and assigns it to the adapter's option
named `password`, erroring if the adapter declares none, and conflicting explicitly with
`-f -` rather than hanging on a stream that has already been consumed. **`hsql` never
prompts**: a prompt that blocks on stdin is the worst failure mode for an agent — no output,
no exit code, the whole turn burned until something times out. The interactive prompt that
#667 asks for belongs to `harlequin`, and this milestone does not add it; what it adds is
the declaration that would drive it, plus the channel a script can use today.

### 3.8 `hsql find` and `fetch_descendants` (stretch)

`find` is what an agent actually needs — *where does `orders` live*, *which tables have a
`customer_id`* — and it cannot be a walk. It wants an optional capability:

```python
class HarlequinConnection:
    def search_catalog(self, term: str, kind: Literal["tables", "columns", "all"]
                       ) -> list[CatalogItem]: ...   # raises NotImplementedError
```

One `information_schema`-style query where the adapter can serve it, an explicit "not
supported by this adapter, try `hsql catalog`" where it can't, surfaced through
`IMPLEMENTS_CATALOG_SEARCH` in `info --json`. Optional `fetch_descendants(depth)` on
`InteractiveCatalogItem`, defaulting to a `fetch_children` walk, is the same bet at the
other end: it is what would turn §1.1's 403 round trips into one for adapters that can do
it, and it improves the IDE's expand-all path too. Both are stretch because neither blocks
anything else, and both are worth pulling forward if Release A runs short.

---

## 4. Sequencing

Three releases (Ted's call), because the three groups have different blockers. The catalog
verbs need only in-tree adapter fields; self-description needs nothing outside this repo;
safety needs contract additions that fifteen adapters have to adopt before the flags are
true everywhere. Splitting them means the highest-leverage verb in the plan does not wait
on an ecosystem rollout.

Numbering assumes M1's remaining work releases as 2.9.

### Release A — `hsql catalog` and `hsql describe` (2.10)

**PR 1 — Verbs, and `catalog` at one level.** `verbs.py`, the third preflight phase, the
collision rule, `Commands:` in `--help` and `hsql <verb> --help`. `harlequin.navigate` with
path resolution and single-level listing. Listings as result sets, `query_name` included,
and the `text_columns()` string short-circuit that keeps duckdb out of a Postgres catalog
call. Guard: `hsql --help` still imports no adapter, and is still ~90ms.

**PR 2 — Types, counts, depth and the budget.** `CatalogItem.type_name` and `child_count`
on the contract, populated by the DuckDB and SQLite adapters from data they already fetch.
`--depth`, `--max-nodes` with its notice, `round_trips` in `--stats`, and the `compact` and
`tree` layouts. This is the PR that makes `hsql catalog … --format compact` print
`total DECIMAL(18,2)` instead of `total #.#`.

**PR 3 — `hsql describe`.** One object in full, one round trip below the resolve.

**PR 4 (stretch) — `hsql find`.** `IMPLEMENTS_CATALOG_SEARCH`, `search_catalog()` on the
connection, implemented for DuckDB and SQLite; explicit refusal elsewhere. Optionally
`fetch_descendants(depth)` with its walking default.

**PR 5 — Docs.** Catalog and Introspection in the "Headless & Agents" topic
(a `harlequin-web` PR), plus the adapter-author page for the two new `CatalogItem` fields.

### Release B — self-description (2.11)

**PR 6 — `AbstractOption.to_dict()` and `hsql spec --json`.** Concrete, never abstract, with
a base implementation that works for a subclass that predates it. `spec` covers hsql's own
options and every installed adapter's; `-a NAME` narrows it to one. (It cannot cover
`harlequin`'s own flags: `hsql` may not import `harlequin.cli`, which builds the IDE's
command. Say so in the output rather than pretending the list is exhaustive.)

**PR 7 — `hsql info --json`.** Versions, platform, discovered config files in precedence
order, active profile, and declared capabilities per adapter. No connection.

**PR 8 — Config: per-key merge and provenance, `config show` and `config validate`.** The
merge fix is a behavior change and a bug fix; it lands with the verb that reveals it.

**PR 9 — `config schema --json` and the packaged base schema.** Depends on PR 6's
serialization.

**PR 10 — `config init --non-interactive`.** tomlkit deferred; no questionary.

**PR 11 — `hsql fmt`.** sqlfmt deferred into the verb, with an `ignore_imports` entry and a
run-time test that `hsql -c "select 1"` still imports no sqlfmt. `-c`, files, stdin,
`--check` for CI.

**PR 12 — Docs.** Self-description pages, the config spec page, and the site's JSON Schema
route.

### Release C — safety (2.12)

**PR 13 — Capability declarations and `--read-only`.** `IMPLEMENTS_READ_ONLY` and
`IMPLEMENTS_VALIDATE_SQL` on the contract, declared by both in-tree adapters, reported by
`info`, and refused where undeclared. Companion issues filed against the out-of-tree
adapters we maintain.

**PR 14 — `--timeout`.** The deadline, the cancel, the grace period, the `os._exit`, and the
attribution that keeps a cancelled query from printing as an empty one.

**PR 15 — `--dry-run`**, plus the DuckDB adapter's `PREPARE` pass.

**PR 16 — `--single-transaction`**, plus a transaction mode for the DuckDB adapter.

**PR 17 — Secret options and redaction.** `secret=` on options, `harlequin.redact`, DSN
redaction, masked wizard input, `--password-stdin`. Closes #667's structural half.

**PR 18 — `${VAR}` interpolation.** Closes #898.

**PR 19 — Docs.** Safety page, the psql differences table, and the secrets guidance.

**Ordering rationale.** Within each release the contract change lands with the first
consumer that needs it, as in M1 — a field nothing reads is a field nobody notices is wrong.
Across releases, the rule is that nothing which needs an ecosystem rollout blocks anything
which doesn't.

---

## 5. Testing

The M1 guards stay and grow; the new ones are the same kind — deterministic, and failing
identically on every machine.

- **Import hygiene, extended per verb.** `hsql -c "select 1"` imports no `sqlfmt`, no
  `tomlkit`, no `questionary`; `hsql --help` and `hsql <verb> --help` import no adapter;
  `hsql -a sqlite catalog` imports no `duckdb`. Each is a subprocess reading `sys.modules`,
  and each has a matching `ignore_imports` entry with a comment at the deferral site.
- **A counting fake connection** — a `HarlequinConnection` whose `fetch_children` increments
  a counter — pins the round-trip claims as *assertions* rather than prose: one per segment
  plus one for the listing, `--depth 2` on a 400-relation schema stops at `--max-nodes`, and
  the notice names the path.
- **A fake adapter that declares nothing** proves every refusal path: `--read-only`,
  `--timeout`, `--dry-run` and `--single-transaction` each exit 2 with a message naming the
  adapter, before any connection is opened.
- **Golden files** for `catalog`, `describe`, `info`, `spec` and `config show` in every
  format they support, as syrupy snapshots alongside the M1 output snapshots. Fixtures with
  unicode labels, a zero-child node, an unknown `child_count`, and an adapter that populates
  no `type_name` — the degradation path is the one nobody exercises.
- **Redaction is asserted negatively and exhaustively**: for a profile with a secret in an
  option *and* in a connection string, the secret's literal value appears in no byte of
  `info --json`, `spec --json`, `config show`, any error message, or `--stats`.
- **Interpolation**: `${VAR}`, `${VAR:-default}`, `$${` as a literal, an unset variable
  exiting 2 and naming both the variable and the file — and the one that matters most, that
  `harlequin --config` on a file containing `${PGPASSWORD}` writes `${PGPASSWORD}` back.
- **Timeout tests use a fake adapter, not a slow query**: `execute` blocks on an event,
  `cancel` sets it. Deterministic, fast, and it exercises the attribution logic, which is
  the part that would otherwise be tested by accident. One integration test against DuckDB
  covers the real interrupt path, marked slow.
- **The merge change gets its own test** with two files defining different profiles, pinning
  that both survive.
- **A listing is as deterministic as a result set**: identical bytes whether stdout is a
  pipe, a file or a pty, `\n` on every platform, unaffected by `LC_ALL`, and `compact`
  naming the same type string that `--format csv` writes for the same column. That last one
  is what stops the two renderings of a catalog from drifting the way the M1 formats were
  kept from drifting.

Not tested, because it was dropped from M1: an agent eval suite. §13 of the product plan
calls it the metric that actually matters, and M2 doubles the surface an agent has to choose
from. If it comes back, `spec --json` is what makes it cheap to write, and Release B is
where it would land.

---

## 6. Decisions

**Settled.**

- *Three releases* — catalog/describe, then self-description, then safety (Ted's call, §4).
- *`--read-only` is a declared capability and refuses when it is absent*, rather than
  mapping to a conventionally-named adapter option (Ted's call, §3.4). The flag works on two
  of fifteen adapters on the day it ships, and says so on the other thirteen instead of
  quietly not working.
- *Secrets: declare, redact, and `--password-stdin`* (Ted's call, §3.7). No prompt in
  `hsql`, ever; the IDE's interactive prompt is not in this milestone.
- *`${VAR}` and `${VAR:-default}`* (Ted's call, §3.5), resolved on the read path only, with
  an unset variable an error rather than an empty string.
- *Catalog listings are result sets*, so M2 adds two layouts and no output subsystem (§3.3).
- *Capabilities are declared, not detected* — reflection is measurably wrong (§1.4).
- *`info` does not connect*, and `spec`, `fmt` and `config` do not either (§3.1).
- *No EXPLAIN in `--dry-run`*: it would mean core composing SQL (§3.6).
- *No shell-command interpolation* in config values (§3.5).

**Still open.**

- **Whether `describe` should report row counts.** The product plan asks for them "where the
  adapter can supply them"; for most adapters that is a `count(*)`, which is a query against
  user data rather than a catalog lookup and can be arbitrarily expensive. My inclination is
  to leave it out of Release A and add `--with-row-counts` later if it is asked for, rather
  than have `describe` surprise someone with a table scan.
- **Whether `hsql catalog` should cache.** The IDE pickles a catalog per connection
  (`catalog_cache.py`), and an agent making six navigation calls in a row re-resolves the
  same prefix six times. Reusing that cache would cut §1.1's per-segment cost to zero on
  repeat calls, and it would also let `hsql` hand a warm catalog to the IDE for the handoff
  in M4 — but a stale catalog that an agent then writes a query against is a worse failure
  than a slow one. Deferred until there is evidence of the cost in practice.

---

## 7. Explicitly not in M2

`hsql history` and `hsql open` (M4, with the skill). `hsql mcp` (M6). Streaming and
pagination (M5). The interactive password prompt on `harlequin` (#667's other half). The
agent eval suite (dropped from M1). The site's `llms.txt`, raw markdown routes and Docs API
(M3) — M2's docs work is the "Headless & Agents" pages for the verbs it adds, on the topic
M1's PR 7 seeds. Publishing the JSON Schema at `harlequin.sh/schemas/config/v1.json` is a
`harlequin-web` PR that this repo's `config schema --json` feeds. Unifying the IDE's export
dialog onto `write_file`, and making `textual` an optional extra of
`textual-fastdatatable`, both still deferred from M1.

---

## 8. Corrections to the product plan

Applied to `harlequin-for-agents.md` in the same PR as this document; recorded here so the
reasoning behind each is written down.

- **§6 says `hsql catalog mydb.analytics.orders` is "exactly one `fetch_children()` call."**
  It is one round trip *per path segment* plus one for the listing — four for that example —
  because an item can only be reached through its ancestors (§1.1). The listing is still
  single-level and still cheap; the resolve in front of it is not free, and on a network
  database it is four round trips rather than one.
- **§6's `--format compact` example is unimplementable against the current catalog
  contract.** `CatalogItem` carries a 1–3 character `type_label`, so the honest rendering
  today is `total #.#`, not `total DECIMAL(18,2)`. It needs a `type_name` field, which the
  in-tree adapters already fetch and discard (§1.3).
- **§6 asks for child counts without a field to put them in.** Same shape: an optional
  `child_count`, where `None` means unknown rather than zero.
- **§6's capability flags cannot be read off a connection reflectively.** SQLite defines
  `validate_sql` and raises `NotImplementedError` from it, so the flags have to be declared
  on the adapter class — which is also what lets `hsql info --json` answer while the database
  is unreachable (§1.4).
- **§5's `--timeout` needs `hsql` to attribute the cancellation itself.** A cancelled DuckDB
  query returns an empty result set and no error, so a timeout that trusts the adapter
  reports "no rows" and exits 0 (§1.9). Worth stating in the product plan, because it is
  exactly the kind of thing that looks like an implementation detail and quietly breaks
  principle 5.
- **§5's `--dry-run` is a parse check, not an existence check**, on the adapter it will be
  used with most: DuckDB's `validate_sql` treats every non-parser error as valid, so a query
  against a missing table dry-runs clean (§1.10).
- **§7's config-file merge is per top-level key, not per profile.** A cwd config file that
  defines any profile hides every profile in the home file (§1.6). The formal spec §7 asks
  for should describe the per-key merge M2 ships, not the one that exists today.
- **§7's `${VAR}` interpolation needs to be a read-path transform**, because
  `harlequin --config` reads the config and writes it back — interpolating too early writes
  the resolved secret into the user's file (§1.6).
- **§4's "`hsql <SUBCOMMAND>`" needs a stated collision rule.** A connection string that
  exactly matches a verb name is ambiguous, which is the objection §4 raises against
  subcommands on `harlequin`. The rule is that an exact match wins and `./catalog` is the
  escape (§3.1).
