# M2 Technical Plan — self-description and safety

Implementation plan for milestone M2 of [Harlequin for agents](./harlequin-for-agents.md),
following [the M1 technical plan](./m1-hsql-technical-plan.md). The product plan says what
`hsql` is for; M1 built the command and the execution core under it. This one is about
letting an agent learn a database before it queries one, and about the flags that let a
human hand an agent a warehouse credential.

**M2's scope, from the roadmap:** `catalog` (one level below the match), `info --json`,
`spec --json`, `config validate/show/schema/init`, capability flags, the
secret option type and declarative redaction
([#667](https://github.com/tconbeer/harlequin/issues/667)), env interpolation
([#898](https://github.com/tconbeer/harlequin/issues/898)), `--read-only`, `--timeout`, and
a published JSON Schema. `find` + `implements_catalog_search` is promoted out of the stretch
column and ships with implementations for both in-tree adapters; optional `fetch_descendants`
stays future work.

**Cut in review** (§7 has the reasoning for each): `describe`, `fmt`, `--dry-run`,
`--single-transaction` (which M1 §7 had deferred to M2), `--password-stdin`, recursive
`--depth`, the node budget, child counts, round-trip counting, and the `compact` and `tree`
layouts.

**Where M1 actually got to.** PRs 1–6 shipped: the import-hygiene work, the statement
splitter and execution core, `export`/`layout`, the shared profile-merge and one-adapter
loading, `hsql` itself, and the two commands pointing at each other. PR 7 (the docs topic,
against `tconbeer/harlequin-web`) is in flight. PR 8, the agent eval suite, is not being
done — so M2 has no automated measure of "an agent used the right flag first try," and the
guards below are all deterministic ones.

**Bottom line up front.** M2 needs no new execution path, no new output format family, and
no second config parser: **catalog listings are result sets**, so `--format`, `-o`, `-t/-A`,
`--display-rows` and `--stats` already apply to them, and the row cap that keeps a 400-row
listing readable is the one M1 already shipped. What M2 does need is **five additive members
on the adapter contract** — four fields and one method — because the features that would
otherwise be built on guesswork are unimplementable without them: the catalog knows a column
is `##` but not that it is `DECIMAL(18,2)`, no adapter can say whether it enforces read-only,
and none can answer "where does `orders` live" without a walk. Those are measured,
below, not feared.

**On the shape of the CLI.** Every capability here is a **mode option** on the one `hsql`
command — `--catalog`, `--find`, `--info`, `--spec`, `--config MODE` — not a subcommand
(Ted's call). It matches `harlequin --config`, and it removes an ambiguity subcommands
would have created: `hsql` takes `CONN_STR` positionally, so a database file named
`catalog` would have collided with the verb. There is no collision to rule on if there is
no verb. See §3.1.

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
the product plan asks for, and it is why one level below the match is cheap.

But there is no way to *reach* an item except by fetching its ancestors. To call
`fetch_children()` on `mydb.analytics.orders` you need that item object, which only
`SchemaCatalogItem.fetch_children()` can hand you, which needs the schema item, which needs
the database item. I built a DuckDB file with 400 relations in one schema and counted the
round trips (spelling the calls in the mode-option syntax of §3.1):

| Call | Children | Round trips | Local ms |
| --- | --- | --- | --- |
| `hsql --catalog wide.db` | 1 database | 1 | 1.9 |
| `hsql --catalog --path wide wide.db` | 2 schemas | 2 | 10.2 |
| `hsql --catalog --path wide.analytics wide.db` | 400 relations | 3 | 12.8 |
| `hsql --catalog --path wide.analytics.t000 wide.db` | 3 columns | 4 | 22.8 |
| the same at a hypothetical `--depth 2` | 400 relations + 1200 columns | **403** | **2303** |

So a listing costs **one round trip per path segment, plus one** — not the "exactly one
`fetch_children()`" the product plan's §6 claims. On local DuckDB that difference is 20ms;
on BigQuery or Trino it is four network calls where the plan promised one.

**What recursion costs depends entirely on where you stand**, which is why the product
plan's blanket `--depth N` is the wrong knob. Depth 2 from the root fetches every database's
schema names: one round trip per database, and databases are few. Depth 2 from a *schema* is
one round trip per relation, and the last row above is what that means — **403 round trips
and 2.3 seconds against a local file**, on the call an agent would most want to make.

M2 therefore ships **one level, always**, and no `--depth` at all (§3.2). The honest
recursive answer needs `fetch_descendants()` on the adapter contract so a database can
answer in one query (§3.8), and that is where the flag should arrive from.

### 1.2 Catalog depth is adapter-specific, and nothing declares it

DuckDB and Postgres are four levels (database → schema → relation → column). SQLite is
three (database → relation → column). BigQuery's own hierarchy is project → dataset →
table, which is four levels wearing different words. Nothing in `harlequin.catalog` says
how deep a catalog goes or what a level is called.

So `--path` cannot document its grammar as `database.schema.table`. Paths are **positional
segments** whose meaning is the adapter's, and the type label on each item (`db`, `sch`,
`t`, `v`) is what tells a reader what they got. That is a small design constraint with a
large documentation consequence, and it beats inventing a level vocabulary that would be
wrong for a third of the ecosystem.

### 1.3 The catalog knows the short type, and throws the real one away

`CatalogItem.type_label` is documented as "a short (1-3 chars) label" — `##` for a bigint,
`s` for a varchar, `ts` for a timestamp. It is a Data Catalog affordance: it has to fit in a
tree column next to the name.

So a listing of `analytics.orders` can say `id ##, customer_id ##, total #.#` and no more,
which tells an agent almost nothing and would send it back to `select * limit 0` to find out
what it is joining on. **The type an agent needs to write a join is not in the catalog
contract** — only the glyph that fits in the IDE's tree.

The data is right there and discarded. `DuckDbConnection._get_columns()` selects
`column_name, data_type` from `information_schema.columns` and then does
`type_label=self.connection._short_column_type(column_type)`, dropping `column_type` on the
floor. SQLite's is the same shape. So this is one field on `CatalogItem`, not a new query
(§3.2).

**Child counts are a different matter, and M2 does not add a field for them.** There is
nowhere to hang a count today, and there shouldn't be: no adapter can produce one without
fetching the children, so a `child_count` field would either be `None` everywhere or be a
fetch in disguise. The count that is knowable is `len(children)` once they are loaded —
which is what a listing already shows in its own footer. The product plan's "report child
counts wherever the adapter can get them cheaply" turns out to have no cheap case, and it
was proposed as a mitigation for a recursion M2 is not shipping anyway.

### 1.4 Capability flags have to be declared, and only some of them are

Today's capability surface is three things on the adapter class: `IMPLEMENTS_CANCEL`,
`implements_copy` (derived from `COPY_FORMATS`, and deprecated), and `provides_details`.
Everything else — `cancel`, `validate_sql`, `copy`, `transaction_mode` — lives on the
*connection*, and the connection class is not reachable from the adapter class without
connecting to a database.

Nor can an override be detected reflectively. `type(conn).validate_sql is not
HarlequinConnection.validate_sql` reports SQLite as validating SQL, because
`HarlequinSqliteConnection.validate_sql` **is** defined and its body is `raise
NotImplementedError`. That is not a bug — the app catches `NotImplementedError` at the call
site and carries on — but it does mean a reflective probe cannot answer the question, and
that a class variable is the pattern that can.

Cost, measured, with those four adapters installed:

| | ms | modules |
| --- | --- | --- |
| entry-point *names* (`adapter_names()`) | 46 | 126 |
| names + dist name and version (`ep.dist`) | 51 | 128 |
| `load_adapter("duckdb")` | 114 | 187 |
| `load_adapter_plugins()` — all four | 309 | 425 |

Two things follow. Adapter **versions are free** — `ep.dist.version` costs 5ms over reading
names, so what is installed is answerable without importing any of it. (Capability flags are
class variables, so reporting *those* costs the import; §3.4.) And anything that wants every
adapter's *options* pays 300ms and rising, which is fine for `--spec`, a once-per-task
lookup, and not fine for anything on the query path.

### 1.5 Options can be declared but not described, validated, or marked secret

`AbstractOption` renders three ways — `to_click()`, `to_widgets()`, `to_questionary()` — and
has no fourth. There is no serialization, so `--spec` has nothing to call, and the
debug-info screen builds a markdown table by reaching for `opt.name`, `opt.short_decls` and
`getattr(opt, "default", None)` — duck-typing at a distance, which is what a real
`to_dict()` would replace.

Adding an **abstract** method is not available: `AbstractOption` is public API and
third-party adapters subclass it. Anything added here has to be concrete, with a base
implementation that works for a subclass that has never heard of it.

Nothing declares a value sensitive. Sampling the ecosystem, `harlequin-postgres` 1.3.1
declares twelve options, of which `password` is a plain `TextOption`, `sslkey` is a
`TextOption`, and `passfile` is a `PathOption`. So `--spec` would today teach an agent that
`--password` exists and say nothing about why it must not be typed on a command line, where
`ps` and shell history can read it.

**And nothing validates an adapter option that arrives from a profile.** Options typed on the
command line go through click, which rejects what it doesn't know; options read out of a
config file go straight to the adapter's constructor as keyword arguments, and the adapter
contract tells adapters to tolerate supersets of what they declare. So a misspelled key is
accepted in silence, by design, all the way down:

```python
>>> DuckDbAdapter(conn_str=(":memory:",), reed_only=True, dbnmae="warehouse")
constructed fine | read_only = False
```

That is the safety-relevant version of §1.6: a human who put `reed_only = true` in a profile
believes they handed an agent a read-only connection, and did not. Nobody in the stack is in
a position to notice — except a step that knows both the profile and the adapter's declared
options, which is §3.5's second pass.

### 1.6 Config files merge per top-level key, and it is worse than losing a profile

Ted asked me to test this rather than assert it, and the result is sharper than the first
draft claimed. `_merge_config_files()` is a top-level `dict.update()` per file, so:

```toml
# ~/.harlequin.toml
default_profile = "personal"
[profiles.personal]   # + [profiles.shared]

# ./.harlequin.toml
[profiles.project]
```

```
merged: {'default_profile': 'personal', 'profiles': {'project': {'adapter': 'sqlite'}}}
```

The cwd file's `profiles` table replaces the home file's outright — but `default_profile`
survives from the home file, because it is a *different* top-level key. So the two do not
just disagree, they contradict, and **both commands then refuse to start**:

```
harlequin.exception.HarlequinConfigError: Config files set the default_profile to
personal, but do not define a profile with that name.
```

A project-local config file that names one profile bricks a user whose personal file sets a
default. **This predates the `tomllib` refactor**: I ran the same probe against the
pre-refactor `config.py` (the tomlkit read path, from `1af8cca`) and got the identical
error, so the C parser did not introduce it — `dict.update()` has always replaced the table
rather than merging it.

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

### 1.7 Two expensive imports the query path must never pay

`hsql` is contractually forbidden from importing `sqlfmt`, `questionary` and Textual: the
`hsql does not reach the TUI` import-linter contract lists them, and
`tests/unit_tests/test_import_hygiene.py` asserts they stay out of `sys.modules` after every
headless import. With `fmt` cut from this milestone (§7), **sqlfmt stays forbidden outright**
— there is now no headless code path that wants it.

Two others are merely expensive, and M2 adds the first consumer of each:

- **`questionary`, 156ms and 329 modules.** `hsql` must never import it, because `hsql` must
  never prompt (§3.7). `--config init` is the non-interactive path, and the wizard stays
  where it is.
- **`tomlkit`, 49ms alone and +8ms on top of `harlequin.hsql.cli`.** Only `--config init`
  writes a file, so only `--config init` pays it, deferred into the mode.

For scale, `harlequin.hsql.cli` alone imports in 107ms across 187 modules, and importing
sqlfmt on top of it would have taken that to 217ms/304 — most of a second `hsql -c`
invocation, for a feature §7 explains we are not shipping.

### 1.8 There is nothing to build `--read-only` on, and transactions are worse

No adapter declares read-only as a capability. DuckDB and SQLite each declare their own
`read-only` *option*, which core could set by name — but `harlequin-postgres` declares no
such option at all, so a convention-based `--read-only` would silently do nothing on the
adapter most likely to be pointed at production. (Postgres can enforce it; it wants `set
default_transaction_read_only = on` at connect, which is adapter work, not core work.)

Transactions are the same shape and further gone, which is why `--single-transaction` is cut
(§7). `HarlequinConnection.transaction_mode` returns a
`HarlequinTransactionMode(label, commit, rollback)` or None, and **the label is whatever the
adapter chose**: SQLite offers Auto/Manual on 3.12+, Postgres offers Auto/Manual, DuckDB
overrides nothing and so returns None, and a third-party adapter may name its modes anything
at all and default to whatever its driver does. Core cannot ask for "the manual one" through
that interface without first making the contract say which mode is which.

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

---

## 2. The obstacles, stated plainly

1. **The adapter contract can't answer four of M2's questions.** What is this column's real
   type; where does a name live; can you enforce read-only; can you validate SQL. All are
   additive, with defaults (plus one more on options, for secrets), and all need an ecosystem
   rollout before they are true everywhere — which is why the release that needs none of them
   goes first.
2. **Resolution is a walk, and recursion is unbounded.** One round trip per path segment,
   and 403 for a single recursive listing of a wide schema. The answer M2 takes is to ship
   one level and no recursion at all, rather than to ship a knob with a cliff behind it.
3. **`hsql` grows four modes without losing a 90ms `--help`.** Two of them need imports the
   query path must never pay — every installed adapter at once, and tomlkit — and one of
   them (`--info`) has to work when the database is unreachable, which is precisely when a
   diagnostic is worth most.
4. **A timeout cannot trust the adapter to report itself.** §1.9. `hsql` has to attribute
   the cancellation it caused, because the adapter can't.
5. **Secrets leak through four new mouths at once.** `--info`, `--spec`, `--config show` and
   errors are all output surfaces M2 adds, and every one of them handles connection options.
   Redaction has to be a property of the declaration, not a rule applied four times.

Note what is *not* on the list. There is no new output subsystem: catalog listings are rows,
so `--format`, `-o`, `-t`, `-A`, `--display-rows` and `--stats` are already written and
already tested (§3.3). And there is no new config parser: provenance, interpolation and
schema generation are all things to do to the config we already read.

---

## 3. Target architecture

```
harlequin/
  adapter.py      MOD  + IMPLEMENTS_READ_ONLY, IMPLEMENTS_VALIDATE_SQL
  catalog.py      MOD  + CatalogItem.type_name
  navigate.py     NEW  resolving a catalog path and listing one level below it
  options.py      MOD  + secret=..., + to_dict()
  config.py       MOD  per-profile merge, provenance, ${VAR} interpolation
  config_schema.py NEW the JSON Schema for a config file, generated from what is installed
  redact.py       NEW  redaction helpers, driven by the options that declare themselves secret
  query.py        MOD  text_columns() returns string data unchanged
  hsql/
    cli.py        MOD  + the mode options, and which of them attaches adapter options
    timeout.py    NEW  a wall clock over a run, and how it exits
    modes/        NEW  catalog.py find.py info.py spec.py config.py
```

M1's rule stands and now covers four modes as well as the run form:

> **stdout is written only by `harlequin.layout` and `harlequin.export`. stderr is written
> only by `harlequin.hsql.diagnostics`.**

M2 adds a second, which is the whole milestone in a sentence and is also testable:

> **No mode pays for what it did not ask for.** `--help` imports no adapter; `--spec` and
> `--config` import no database driver; `--info` opens no connection; `--catalog` fetches
> one level; and the query path imports neither sqlfmt, tomlkit nor questionary.

### 3.1 Modes are options, not subcommands

```
hsql [OPTIONS] [CONN_STR]...

  --catalog          List catalog objects one level below --path.
  --path TEXT        Where in the catalog to look. Dotted segments; default is the top.
  --find TERM        Search the catalog for TERM, where the adapter can.
  --info             Report versions, config files, adapters and capabilities. JSON.
  --spec             Dump the installed CLI surface, including adapter options. JSON.
  --config MODE      show | list-profiles | validate | schema | init
```

**Why options rather than subcommands (Ted's call).** It matches `harlequin --config`, so
one shape covers both commands. It keeps `hsql`'s parse a plain click command — M1's
two-phase build stays two phases rather than growing a verb phase in front. And it removes
an ambiguity that subcommands would have introduced for free: `hsql` takes `CONN_STR`
positionally, so `hsql catalog` and a DuckDB file named `catalog` would have needed a
disambiguation rule, docs for the rule, and a test for the rule. No verb, no rule.

Modes are **mutually exclusive**; two of them is a usage error, exit 2, naming both. With no
mode, `hsql` is what M1 shipped.

**`--path`, not `-c`.** Ted floated reusing `-c` for the catalog path and offered `--path`
or `--relation` as alternatives; `--path` is the one to take. `-c` is `--command` and means
*SQL* in this CLI, in psql, and in duckdb, and the whole premise of principle 4 is that a
flag should not change meaning with context. An agent that has learned `-c` types
`hsql --catalog -c "select 1"` sooner or later, and the right outcome is an error, not a
lookup for a relation named `select 1`. `--path` also leaves `-c` free to mean what it
means, if a later milestone wants `--catalog` and a query in one invocation.

**Which mode needs what**, because this is the table that keeps §3's second rule true:

| mode | adapter import | connection | notable import |
| --- | --- | --- | --- |
| (none — run SQL) | one | yes | — |
| `--catalog`, `--find` | one | yes | — |
| `--info` | all, for capability flags; one under `-a` | **no** | — |
| `--spec` | all, or one under `-a` | no | — |
| `--config show/validate/schema` | all, for adapter options | no | — |
| `--config init` | one | no | tomlkit |

`--info` not connecting is a design decision, not an omission: the diagnostic an agent runs
when the database is unreachable must not itself require the database. Capability flags come
from declarations (§3.4) precisely so that this holds — reading a declaration costs an import
of the adapter class, but never a connection.

`--info` and `--spec` emit JSON documents rather than rows, so `--format` does not apply to
them; a caller who sets it explicitly gets the same kind of stderr note `--display-rows`
already produces when it cannot reach the chosen format. `--catalog` and `--find` produce
rows, and every format applies (§3.3).

The modes live one module apiece under `harlequin/hsql/modes/`, imported by the callback
when that mode is chosen — which is how `--config init`'s tomlkit and `--spec`'s
all-adapters import stay off every other invocation.

### 3.2 `harlequin.navigate` — one level, and what it cost

```python
@dataclass(frozen=True)
class CatalogPath:
    segments: tuple[str, ...]  # positional; the adapter names the levels
    glob: str | None = None  # a trailing wildcard only


@dataclass
class Listing:
    parent: CatalogItem | None  # None at the top level
    items: list[CatalogItem]


def resolve(connection, path) -> CatalogItem | None: ...
def list_children(connection, path) -> Listing: ...
```

Three things this module owns, and nothing else does:

**One level, always.** No `--depth`. §1.1 is the argument: recursion's cost depends on the
level it starts from, so a single number cannot be safe at every level, and the one place an
agent most wants it is the one place it is 403 round trips. An agent that wants a schema's
columns asks for one relation at a time, which is what the IDE's own catalog does and what
Ted proposed in review. Recursion comes back when an adapter can answer it in one query
(§3.8), and it arrives with the capability rather than ahead of it.

**A trailing glob is sugar; an interior one is a different mode.** `--path analytics.ord*`
filters one resolved parent's children in the client and stays one round trip.
`--path *.orders` cannot be evaluated without fetching every candidate level, so it belongs
to `--find` (§3.8) and is refused here rather than quietly walked.

There is nothing to instrument, either. An earlier draft had `--stats` report `round_trips`,
which existed to make a recursive walk's cost visible after the fact; without recursion the
number is `len(path) + 1` and a caller can read it off the path they typed.

There is no node budget, because there is nothing left to bound: one level is one round
trip, and a listing with four hundred rows in it is a *display* problem that M1 already
solved. `--display-rows` caps what a text layout prints and the footer says `(40 of 400
rows)`, exactly as it does for a query.

The one field this needs on the contract:

```python
@dataclass
class CatalogItem:
    ...
    type_name: str | None = None  # the adapter's own full type, e.g. DECIMAL(18,2)
```

It defaults to None and is populated in-tree from data the adapters already fetch (§1.3). A
listing prints it beside `type_label` rather than instead of it, so an adapter that never
adopts the field degrades to today's short label rather than to a blank column.

*One caveat for adapter authors:* `CatalogItem` is a dataclass and its subclasses append
fields, so a new base field shifts positional argument order for subclass constructors.
Every in-tree call site uses keywords (`from_parent(...)`), and the adapter template does
too, but the changelog entry should say so.

### 3.3 Catalog listings are result sets, so the output layer is already written

A listing is rows: `path`, `name`, `type`, `type_name`, `query_name`. Making it a
`ResultSet` — `create_backend()` takes a sequence of rows and the column names, with no
database involved — means `--catalog` inherits `--format csv|json|jsonl|markdown|table|
vertical|parquet`, `-o PATH`, `-t/-A/--no-header`, `--display-rows` and the byte-for-byte
determinism M1's snapshots already pin. The alternative is a second renderer for the same
job, which is how `--format json` and a catalog's JSON end up disagreeing about how to spell
null.

**And no new layouts.** An earlier draft added `compact` and `tree`; both are out.

`tree` had nothing to draw: one level is a flat list of children, not a tree. And `compact`
— `analytics.orders(id BIGINT, customer_id BIGINT, total DECIMAL(18,2))` — only reads that
way for a relation and its columns. §1.2 is the reason it can't generalize: what a level
*is* belongs to the adapter, so `parent(child TYPE, …)` would render a database's schemas
as `mydb(analytics sch, main sch)`, which is a format pretending to a structure the catalog
does not promise. The token efficiency it was after is already available over the same rows
— `hsql --catalog --path analytics.orders -tA --format csv` is names and types and nothing
else — and it stays honest at every level, because a row is just a row.

One change in `query.py` makes this cheap: **`text_columns()` returns string data
unchanged.** Today every text layout casts through duckdb, which is right for query results
(§3.3 of the M1 plan) and pointless for a listing that is already strings — and it would
import duckdb (~85ms) on a `hsql -a postgres --catalog` that has no other reason to. The
short-circuit is one type check, and it is also a small win for `select` results that are
all VARCHAR.

**`query_name`, always.** Adapters already compute the correctly quoted identifier for every
item; emitting it means the agent never guesses whether this backend wants `"Orders"` or
`` `orders` ``. It costs nothing and no other client does it.

**Describing a relation is listing it.** `--catalog --path mydb.analytics.orders` is the
columns of `orders`, with their names, types and quoted identifiers — which is everything a
`describe` was going to print. There is no second mode for it (§7).

### 3.4 Capabilities are declared, not detected

```python
class HarlequinAdapter(ABC):
    IMPLEMENTS_CANCEL = False  # exists
    IMPLEMENTS_READ_ONLY = False  # new: connect() honors read_only=True
    IMPLEMENTS_VALIDATE_SQL = False  # new: the connection's validate_sql() is real
    IMPLEMENTS_CATALOG_SEARCH = False  # new: search_catalog() (§3.8)
```

Declarations rather than reflection, because reflection cannot answer the question: SQLite
defines `validate_sql` and raises `NotImplementedError` from it (§1.4). The app already
handles that at the call site, so nothing is broken today — but an adapter that *says* what
it does is the better pattern, and it is the only one `--info` can read without connecting.

Two consequences worth stating:

- **A flag whose capability is undeclared refuses, before connecting.** `hsql --read-only -a
  postgres` exits 2 with `postgres does not declare read-only support; see hsql --info` —
  rather than connecting, running the query, and hoping. Refusing early is the entire point
  of the flag: `--read-only` exists so a human can hand over a credential, and a flag that
  no-ops is worse than one that is absent.
- **Core still catches `NotImplementedError` at the call site**, as the app does. A
  declaration can be wrong, and the failure should be a clear error rather than a traceback.

`IMPLEMENTS_VALIDATE_SQL` has no consumer in `hsql` now that `--dry-run` is cut (§7). It is
still worth declaring: `--info` reports it, and the app can consult it instead of calling a
method it expects to raise.

`hsql --info` reports, per installed adapter: name, distribution, version, declared
capabilities, and whether the adapter imported at all. Plus Harlequin's version, Python's,
the platform, the discovered config files **in precedence order**, and the active profile
with every secret redacted (§3.7).

It opens no connection, but it does import: capabilities are class variables, so reading
them means loading the adapter class. That is the one cost this mode pays — 309ms for four
installed adapters, against 51ms for names and versions alone (§1.4) — and it is the right
trade for a once-per-task lookup whose entire job is that the agent stops guessing.
`hsql --info -a duckdb` imports one (114ms) for the common case of asking about the adapter
you are using, and an adapter that fails to import is reported with its capabilities as
`"unknown"` and the import error alongside — never as `false`, because guessing false about
`implements_read_only` is the direction that gets someone hurt.

### 3.5 Config: validate each file, then merge, with provenance

**Validation moves in front of the merge** (Ted's call). Today `load_config()` merges every
discovered file into one dict and then calls `_raise_on_bad_schema()` on the result, which is
backwards twice over: the error can only name a key, never the file it came from, and the
thing being validated is a structure no user wrote. Validating each file as it is read means
every message can say *which file* and *which key*, and the merge then only ever combines
inputs already known to be well-formed.

```python
def load_config(config_path: Path | None) -> Config:
    return merge([validate(ConfigFile(p)) for p in _find_config_files(config_path)])
```

That reordering is free, and it is most of what makes `--config validate` worth having.

**The merge becomes per profile, and files are read nearest first** (§1.6; amended in PR 1,
Ted's call). `profiles` merges per profile name and `keymaps` per keymap name, and the
nearest file that defines one supplies it whole. An earlier draft of this section had a
profile merge per *option* as well; that is what PR 1 traded away, because it is
incompatible with the early stop below — a profile's keys are not known to be complete until
every file has been read, so per-option merging means no file can ever be skipped. Half a
connection from each of two files is also not a connection either of them describes.

This is a behavior change for both commands and a bug fix for a failure mode that is worse
than losing a profile: before it, a project-local file that names one profile, plus a home
file that sets `default_profile`, makes both commands refuse to start. It gets a Bug Fixes
changelog entry and a test that pins the new semantics against both files.

**Discovery is reversed, and the profile read path stops early** (Ted's call, PR 1).
`_find_config_files()` returns candidates highest priority first, so the first definition of
anything is the winning one, and `load_profile()` — the read path `hsql` uses when it
want a profile and not the keymaps beside it — returns at the file that defines it. The
files behind that one are never opened, parsed or validated. `-P None` reads nothing at all.
`load_config()` is the whole document, for `--config show` and the IDE's keymaps, and has no
reason to stop.

Resolving a name is separated from reading the document in the same PR, because otherwise the
two commands disagree about a config file neither of them is using: a `default_profile` that
names no profile is raised where the name is *used*, so `-P other` and `-P None` start rather
than being refused over a key they overrode. What remains is the honest half of the
difference — `hsql` cannot report a problem in a file it stopped before opening — and
`--config validate` (PR 3) is the mode that reads everything and reports everything. Measured with four candidate files present, the skipped reads pay for most
of msgspec's import: with four candidate files present, `hsql -c "select 1"` and `hsql --help`
are both within noise of the pre-PR-1 command (±3ms), and so is a run that finds exactly one
config file.

**Validation itself runs in two passes, and the second one is new ground** (Ted's design).
Pass 1 validates what core owns, per file, before the merge: the top-level keys, the shape
of `profiles` and `keymaps`, and the profile keys with meaning to `harlequin` or `hsql`. It
cannot reject what it does not recognize, because every adapter's options live in the same
table.

Pass 2 is what makes that safe. Once the adapter is known — which the first pass already
settles, and `load_adapter()` has already imported for the run about to happen — its
`ADAPTER_OPTIONS` say exactly which further keys are legal, so the selected profile can be
validated in full:

```python
declared = {sluggify(o.name) for o in adapter_cls.ADAPTER_OPTIONS}
allowed = core_keys | TUI_ONLY_KEYS | declared
```

**This validates a surface nothing validates today** (§1.5): `reed_only = true` in a profile
is silently discarded by the adapter's `**kwargs`, and the user believes they are connected
read-only. Pass 2 is the only place in the stack that knows both the profile and the
adapter's declared options, so it is the only place that can say `unknown option 'reed_only'
for adapter duckdb; did you mean 'read_only'?`

Two things bound how strict it can be:

- **"Valid" is a union of three sets** — core keys, `TUI_ONLY_KEYS` (a profile written for
  the IDE has to work headlessly), and the adapter's declared options. Get the union wrong
  and configs that work today start failing, so each set gets a test.
- **Values may legitimately arrive uncast.** `adapter.py` tells adapters to "check the types
  of options, as they may not be cast to the correct types", and TOML gives us whatever the
  user typed — `port = 5432` and `port = "5432"` both work today and must keep working. So
  pass 2 checks **names and declared choices**, which is where the value is, and stays
  permissive about scalar types.

Which mode validates what follows from the passes: the run path validates the one selected
profile, with the one adapter it was going to import anyway, while `--config validate`
validates every profile and imports each adapter its profiles name — 309ms for four (§1.4),
which is the right trade for the mode whose whole job is to check.

**On declaring the schema as a model rather than hand-rolling it.** Pydantic, msgspec and
cattrs were all measured against a Harlequin-shaped config; the numbers and the facts that
decide it are in §6.1. The short version: **Pydantic costs +135ms on every invocation and so
fails on the axis it was proposed to improve**, cattrs cannot generate the JSON Schema this
section needs, and **msgspec is the one that pays for itself** at +37ms — strict where it
matters, and `msgspec.defstruct()` builds pass 2's per-adapter struct in 0.022ms, which is
what makes the two-pass design declarative rather than another hand-written loop. Ted is
leaning toward msgspec; §6.1 records what would reverse that, and PR 1 is where it becomes
real.

Provenance falls out of doing the merge properly:

```python
@dataclass(frozen=True)
class Provenance:
    value: Any
    path: Path  # which file this key's winning value came from
    overrode: list[Path]
```

`--config show` prints the effective config with a `# from ~/.harlequin.toml` per key, and
`--config show --json` emits the same as `{key: {value, from, overridden_by}}`. That is the
single best troubleshooting artifact in the plan, and it is also how a human discovers which
file is winning.

**`--config list-profiles`** (Ted's call) prints the names of every profile found, which is
the question a human actually asks first — *what can I pass to `-P`* — and the one `show`
answers only by making them read a merged document. It is rows, so `-tA` gives a bare list a
shell loop can consume, and it marks which profile is the default. It is also the mode that
would have made #1040 obvious: a profile that a project-local file quietly displaced is a
name missing from a short list.

**`${VAR}` and `${VAR:-default}`** (Ted's call), resolved on read, over every string value
in a profile, recursively through arrays and tables:

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
- **Amended in PR 16: the read path is where a profile is *selected*, not where a file is
  read.** Resolving each file as it was read would have refused an invocation over a variable
  named in a profile it was not running — and the IDE reads every discovered file for its
  keymaps, so one `${MYPASSWORD}` in a home config would have stopped `harlequin` from
  starting under any profile. Resolving in `_select_profile()` instead is the rule
  `default_profile` already follows (a name is raised where it is *used*), and it costs the
  merge's `Provenance` — once every file is merged, that is the only thing that still knows
  which file a profile was written in, which an unset variable's message has to name. Two
  consequences worth writing down: `--config show` and the IDE's debug
  screen report what the files say, so they print `${MYPASSWORD}` rather than resolving it —
  which is also the reading that keeps an unset variable from breaking a report *about* the
  file naming it — and `default_profile` and the `keymaps` tables are not interpolated at all,
  which nothing needs and which is what buys the laziness. `--config validate` resolves every
  profile in every file, and reports an unset variable like any other problem.
- Shell-command interpolation (asked for on #898) is out: it is arbitrary code execution
  driven by a file discovered from the working directory.

**`--config validate`** reports `{file, key_path, message, line?}` per problem — one entry
per file per problem, which is what validating before merging buys. Line numbers only where
the parser gives one: `tomllib` puts a line in its parse-error message, and neither parser
reports positions for a key we merely dislike. Saying "line unknown" is better than a made-up
number and better than not shipping the mode.

**`--config schema`** generates a JSON Schema for `.harlequin.toml` from the config model
plus the installed adapters' options — which is why `AbstractOption.to_dict()` (§1.5, §3.7)
has to land first — so it is locally accurate: it knows this machine has
`harlequin-databricks` and what its options are. With a declared model this is
`msgspec.json.schema()` plus the adapter options grafted on; hand-rolled, it is a small
generator over the same two sources. A static base schema also ships in the
package, at `harlequin/schemas/config-v1.json`, for the site to publish at
`https://harlequin.sh/schemas/config/v1.json` — that publication is a `harlequin-web` PR,
outside this repo.

**`--config init`** writes a profile through the existing `ConfigFile` tomlkit path, so
comments and key order survive, taking `--profile NAME`, `--adapter`, and the adapter's own
options as flags. It imports no questionary — the wizard stays exactly where it is, and each
command gets the affordance right for its audience. It is also the exact counterpart of
`harlequin --config`, which is the argument for spelling the modes as options in the first
place.

### 3.6 Safety: read-only and timeout

**`--read-only`** sets `read_only=True` in the options handed to the adapter, and refuses
before connecting when `IMPLEMENTS_READ_ONLY` is false (§3.4). In-tree, DuckDB and SQLite
already accept the kwarg and already do the right thing with it, so they declare the flag and
are done. Out-of-tree adapters are a long tail, and `hsql --info` is what makes the tail
legible.

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

`--single-transaction` is cut (§7): transaction modes are named by each adapter and default
to undefined behavior, so core cannot drive them generically, and a caller who wants one
transaction can write `begin` and `commit` in the script they are already sending.

### 3.7 `harlequin.redact` — the helpers, and what tells them what to hide

```python
def redact_profile(profile: Profile, options: Sequence[AbstractOption]) -> Profile: ...
def redact_conn_str(conn_str: Sequence[str]) -> list[str]: ...
def redact_text(text: str, secrets: Container[str]) -> str: ...
```

Redaction helpers, and nothing else — *what* to hide is not a list kept here. It is a
declaration on the option itself: `AbstractOption` gains `secret: bool = False`, as a class
attribute *and* a keyword, so a third-party subclass that has never passed it still answers
False rather than raising. That is the whole point of doing it declaratively — core cannot
enumerate every adapter's secret (`--service-account-key`, `--token`, `--tls-key`, whatever
the next adapter invents), but each adapter can declare its own once and every consumer gets
it free: `to_dict()` reports it, which is what teaches an agent not to construct
`hsql --password hunter2`; `to_questionary()` masks the input; and `--info`, `--spec`,
`--config show` and error rendering all route through `redact_profile`.

Two things the declaration cannot cover, both with a helper of their own above:

- **Connection strings are positional**, so no option type describes
  `postgres://user:pw@host/db`. `redact_conn_str` is DSN-aware — and
  [#354](https://github.com/tconbeer/harlequin/issues/354) is evidence people really do put
  passwords there.
- **A driver exception that echoes a DSN** does not pass through our option layer, so
  `redact_text` is the backstop applied to error messages before they are printed.

**`hsql` never prompts, and adds no new channel for a password either.** A prompt that blocks
on stdin is the worst failure mode for an agent — no output, no exit code, the whole turn
burned until something times out — so a secret an invocation needs and cannot find is a
fast failure naming the profile or the driver's own environment variable. An earlier draft
added `--password-stdin`; it is out (§7). Between profiles, `${VAR}` interpolation (§3.5) and
the variables the drivers already read (`PGPASSWORD`, a pgpass file, and each adapter's
equivalent), there is no gap it would fill that is worth a flag whose whole job is to consume
the stream `-f -` also wants.

What this milestone contributes to #667 is the declaration and the redaction that follow from
it. The interactive prompt belongs to `harlequin`, and is not here.

### 3.8 `--find`, and the capability recursion is waiting for

`--find` is what an agent actually needs — *where does `orders` live*, *which tables have a
`customer_id`* — and it cannot be a walk. It wants an optional capability:

```python
class HarlequinConnection:
    def search_catalog(
        self, term: str, kind: Literal["tables", "columns", "all"]
    ) -> list[CatalogItem]: ...  # raises NotImplementedError
```

One `information_schema`-style query where the adapter can serve it, an explicit "not
supported by this adapter, try `--catalog`" where it can't, surfaced through
`IMPLEMENTS_CATALOG_SEARCH` in `--info`.

`fetch_descendants(depth)` on `InteractiveCatalogItem`, defaulting to a `fetch_children`
walk, is the same bet at the other end, and it is **the precondition for recursion ever
coming back** (§3.2): it is what turns §1.1's 403 round trips into one for an adapter that
can express the whole level in a single query, and it improves the IDE's expand-all path
too. A `--depth` flag on top of a walking default would just be the cliff again with a
prettier name, which is why it waits for this rather than shipping beside it.

*On the other idea from review — concurrency.* Opening several connections to parallelize a
recursive walk would help wall-clock time and nothing else, and it costs more than it looks:
the adapter contract says nothing about a connection being usable from two threads, nor does
it offer a way to open a second one from an existing connection, so `hsql` would have to
re-`connect()` (re-authenticating N times) and hope. It also multiplies load on the database
for a call the agent should not be making in bulk. Worth revisiting if `fetch_descendants`
lands and is still too slow; not worth it as the first answer.

---

## 4. Sequencing

Three releases, **config and self-description first** (Ted's call). That group fixes bugs
that exist today — the merge in #1040, and error messages that can't name the file they came
from — and needs no adapter change at all, so it can ship while the catalog's contract
addition is still being rolled out. The catalog follows, with search implemented for both
in-tree adapters rather than left as a stretch. Safety is last, because it is the only group
whose flags are false on most adapters until the ecosystem catches up.

**PRs 15 and 16 were pulled forward into Release A** (Ted's call, taken after PR 7). They are
the two that never needed the rest of Release C: neither declares a capability, so neither
waits on the ecosystem. They belong with the self-description modes instead — the whole of
what `--config show`, `--info` and `--spec` do is print a user's own config back, and the
release that adds three ways to print a profile should not be the release before the one that
learns which of its values must not be printed. `${VAR}` follows redaction because it is the
other half of the same answer: redaction keeps a secret out of Harlequin's output, and
interpolation keeps it out of the config file to begin with. They keep their numbers, which
the rest of this document refers to; only the release they ship in changed.

Numbering assumes M1's remaining work releases as 2.9.

### Release A — config and self-description (2.10)

**PR 1 — Validate per file, then merge per profile.** The reordering, the per-profile merge, the
second pass over the selected profile's adapter options, and the errors that can finally name
a file. This is where msgspec arrives as a dependency, or doesn't. Fixes
[#1040](https://github.com/tconbeer/harlequin/issues/1040), which is a hard stop for anyone
whose home config sets `default_profile` and who then adds a project-local file. Behavior
change, Bug Fixes entry, and the failing case as its test.

**PR 2 — `--config show` and `--config list-profiles`.** Provenance per key, and the short
list of names that answers "what can I pass to `-P`".

**PR 3 — `--config validate`.** The machine channel over PR 1's per-file diagnostics.

**PR 4 — `AbstractOption.to_dict()` and `--spec`.** Concrete, never abstract, with a base
implementation that works for a subclass that predates it. `--spec` covers hsql's own options
and every installed adapter's; `-a NAME` narrows it to one. (It cannot cover `harlequin`'s
own flags: `hsql` may not import `harlequin.cli`, which builds the IDE's command. Say so in
the output rather than pretending the list is exhaustive.)

**PR 5 — `--info`.** Versions, platform, discovered config files in precedence order, active
profile, and declared capabilities per adapter. No connection.

**PR 6 — `--config schema` and the packaged base schema.** Depends on PR 4's serialization.

**PR 7 — `--config init`.** tomlkit deferred; no questionary.

**PR 15 — Secret options and redaction.** `secret=` on options, `harlequin.redact`, DSN
redaction, masked wizard input. Closes #667's structural half. Pulled forward from Release C:
every mode Release A adds prints a profile, and this is what decides what they print.

**PR 16 — `${VAR}` interpolation.** Closes #898. Pulled forward with PR 15 — a caller told to
keep a token out of their config file needs the spelling that replaces it in the same release.

**PR 8 — Docs.** The config spec page, the self-description pages, the site's JSON Schema
route, and — arriving with PRs 15 and 16 rather than in PR 17 — the secrets guidance and the
`${VAR}` spelling.

*If the model question in §3.5 resolves toward `msgspec`, it lands in PR 1 — the declaration
is what PR 1's validation and PR 6's schema both read, and retrofitting it later means
writing both twice.*

### Release B — the catalog (2.11)

**PR 9 — Mode options, and `--catalog` at one level.** The mode flags and their mutual
exclusion, `--path`, and which modes attach adapter options. `harlequin.navigate` with path
resolution and single-level listing. Listings as result sets, `query_name` included, and the
`text_columns()` string short-circuit that keeps duckdb out of a Postgres catalog call.
Guard: `hsql --help` still imports no adapter, and is still ~90ms.

**PR 10 — Real type names.** `CatalogItem.type_name` on the contract, populated by the DuckDB
and SQLite adapters from data they already fetch. This is the PR that makes a listing say
`total DECIMAL(18,2)` instead of `total #.#`.

**PR 11 — `--find`.** `IMPLEMENTS_CATALOG_SEARCH` and `search_catalog()` on the connection,
**implemented for DuckDB and SQLite** rather than declared and left to the ecosystem;
explicit refusal on adapters that don't declare it.

**PR 12 — Docs.** Catalog and Introspection in the "Headless & Agents" topic (a
`harlequin-web` PR), plus the adapter-author note for `type_name` and `search_catalog()`.

### Release C — safety (2.12)

**PR 13 — Capability declarations and `--read-only`.** `IMPLEMENTS_READ_ONLY` and
`IMPLEMENTS_VALIDATE_SQL` on the contract, declared by both in-tree adapters, reported by
`--info`, and refused where undeclared. Companion issues filed against the out-of-tree
adapters we maintain.

**PR 14 — `--timeout`.** The deadline, the cancel, the grace period, the `os._exit`, and the
attribution that keeps a cancelled query from printing as an empty one.

*PRs 15 and 16 shipped in Release A — see there.*

**PR 17 — Docs.** Safety page and the psql differences table. The secrets guidance went with
PR 8, alongside the release that shipped it.

**Ordering rationale.** Within each release the contract change lands with the first consumer
that needs it, as in M1 — a field nothing reads is a field nobody notices is wrong. Across
releases: fix what is broken before adding what is new, and let nothing that needs an
ecosystem rollout block anything that doesn't.

---

## 5. Testing

The M1 guards stay and grow; the new ones are the same kind — deterministic, and failing
identically on every machine.

- **Import hygiene, extended per mode.** `hsql -c "select 1"` imports no `tomlkit`, no
  `questionary` and (still) no `sqlfmt`; `hsql --help` imports no adapter; `hsql -a sqlite
  --catalog` imports no `duckdb`. Each is a subprocess reading `sys.modules`, and each
  deferral has a matching `ignore_imports` entry with a comment at the import site.
- **A fake adapter that declares nothing** proves every refusal path: `--read-only`,
  `--timeout` and `--find` each exit 2 with a message naming the adapter, before any
  connection is opened.
- **Golden files** for `--catalog`, `--info`, `--spec` and `--config show` in
  every format they support, as syrupy snapshots alongside the M1 output snapshots. Fixtures
  with unicode labels, a zero-child node, and an adapter that populates no `type_name` — the
  degradation path is the one nobody exercises.
- **Redaction is asserted negatively and exhaustively**: for a profile with a secret in an
  option *and* in a connection string, the secret's literal value appears in no byte of
  `--info`, `--spec`, `--config show`, any error message, or `--stats`.
- **Interpolation**: `${VAR}`, `${VAR:-default}`, `$${` as a literal, an unset variable
  exiting 2 and naming both the variable and the file, and — after PR 16's amendment above —
  a profile the invocation is *not* running being left alone. The one that matters most is
  that `harlequin --config` on a file containing `${MYPASSWORD}` writes `${MYPASSWORD}` back.
- **The merge fix gets the failing case as its test**: a home file with `default_profile` and
  two profiles, a cwd file with a third, asserting all three survive and the default still
  resolves. That combination raises `HarlequinConfigError` on `main` today.
- **Timeout tests use a fake adapter, not a slow query**: `execute` blocks on an event,
  `cancel` sets it. Deterministic, fast, and it exercises the attribution logic, which is the
  part that would otherwise be tested by accident. One integration test against DuckDB covers
  the real interrupt path, marked slow.
- **A listing is as deterministic as a result set**: identical bytes whether stdout is a
  pipe, a file or a pty, `\n` on every platform, and unaffected by `LC_ALL` — the M1
  properties, now over rows that never went through a database.
- **Pass 2's allowed set is asserted per source**: a core key, a `TUI_ONLY_KEYS` key and a
  declared adapter option are each accepted in a profile, and a misspelling of one is
  rejected naming the adapter — with `port = 5432` and `port = "5432"` both still accepted,
  since the adapter contract promises values may arrive uncast.
- **The merge fix is asserted per profile, not just per table**: a profile defined in two
  files is the nearer file's, whole, and the profiles only the farther file defines survive
  beside it — which is the half of "later wins" that a table-level replace gets wrong.
- **Early stopping is asserted with an unreadable file**: a home file containing invalid
  TOML, and a cwd file defining the wanted profile. A file that is opened at all takes the
  test down with it, which is a guard that cannot pass by accident.

Not tested, because it was dropped from M1: an agent eval suite. §13 of the product plan
calls it the metric that actually matters, and M2 widens the surface an agent has to choose
from. If it comes back, `--spec` is what makes it cheap to write, and Release B is where it
would land.

---

## 6. Decisions

**Settled.**

- *Modes are options, not subcommands* (Ted's call, §3.1) — consistent with
  `harlequin --config`, and no verb-versus-conn_str ambiguity to adjudicate.
- *The catalog path is `--path`*, not a reused `-c`, so no flag means SQL in one invocation
  and an identifier in another (§3.1).
- *One level, no `--depth`, no node budget* (§3.2). Recursion returns with
  `fetch_descendants()`, which is what would make it one round trip instead of 403.
- *No `child_count` field* (§1.3). A count is only knowable by fetching, and `len(children)`
  after a listing is the honest version of it.
- *Three releases, config and self-description first* (Ted's call, §4) — they fix existing
  bugs and need no adapter change; then the catalog, with search implemented for both
  in-tree adapters; then safety.
- *Secrets and `${VAR}` ship in Release A, not Release C* (Ted's call, §4, taken after PR 7).
  They declare no capability, so nothing in them waits on the ecosystem, and the release that
  adds three modes for printing a profile is the one that has to know what not to print.
- *No `--describe`* (Ted's call, §7). `--catalog --path db.schema.relation` is a describe.
- *No new layouts* (Ted's call, §3.3). `tree` has no tree to draw over one level, and
  `compact` only reads as `parent(child TYPE, …)` for a relation and its columns, which §1.2
  says is not a shape the catalog contract promises.
- *No round-trip counting* (Ted's call, §3.2). Without recursion the number is `len(path) +
  1`, which a caller can read off the path they typed.
- *Validation runs in two passes* (Ted's design, §3.5): per file ahead of the merge for the
  keys core owns, so an error can name the file it came from, and then the selected profile
  against the adapter's declared options — which catches a misspelled adapter option that
  today is discarded in silence (§1.5).
- *`--config list-profiles`* (Ted's call, §3.5).
- *`--read-only` is a declared capability and refuses when it is absent* (Ted's call, §3.4).
  The flag works on two of fifteen adapters on the day it ships, and says so on the other
  thirteen instead of quietly not working.
- *No `--single-transaction`, no `--password-stdin`* (Ted's call, §7).
- *Secrets: declare and redact* (Ted's call, §3.7). No prompt in `hsql`, ever, and no new
  input channel; the IDE's interactive prompt is not in this milestone.
- *`${VAR}` and `${VAR:-default}`* (Ted's call, §3.5), resolved on the read path only, with
  an unset variable an error rather than an empty string.
- *Catalog listings are result sets*, so M2 adds no output subsystem at all (§3.3).
- *Capabilities are declared, not detected* — reflection cannot tell a real implementation
  from a raising stub (§1.4).
- *`--info` does not connect*, and `--spec` and `--config` do not either (§3.1).
- *No `fmt`, no `--dry-run`* (Ted's call, §7).
- *No shell-command interpolation* in config values (§3.5).

- *Config files are read nearest first, and the profile read path stops at the file that
  defines what it was asked for* (Ted's call, §3.5). The cost of that is per-option merging
  within a profile, which PR 1 gave up to get it: a profile is the nearest file's, whole.
- *The config is declared as msgspec models* (settled in PR 1, §3.5 and §6.1). `Config` is
  the TypedDict *and* the model, so there is one declaration of what a config file may say
  and `msgspec.convert(raw, Config)` is the whole of pass 1; pass 2 builds a struct from the
  adapter's own `ADAPTER_OPTIONS` and parses the profile into it, so an option arrives as the
  type its adapter declared. `Config` is a `Struct` with `forbid_unknown_fields`, so msgspec
  refuses a key nobody declared and the import is module-scope: ~13ms on an invocation that
  finds no config file at all, and within noise of `main` on one that finds any, because the
  files early stopping skips pay for it. One thing is still written by hand, because the
  message is the point: the near miss on a key -- `read-only`, `reed_only`, `keymap_names` --
  which is where `difflib` says *did you mean `read_only`*, computed only once a conversion
  has already failed.

**Still open.**

- Nothing from PR 1. §6.1's reversal conditions — the import cost mattering more once
  `--info` and `--spec` are real, or wanting Pydantic's `extra="allow"` — are still the
  things that would reopen it.

### 6.1 The config model, measured

Three libraries were suggested or considered for declaring `Config`/`Profile` rather than
hand-rolling `_raise_on_bad_schema()`. All of them were run against a Harlequin-shaped
config on this checkout, because the deciding facts are not the ones the READMEs lead with.

| | Pydantic 2.13 | msgspec 0.21 | cattrs 26.1 | hand-rolled |
| --- | --- | --- | --- | --- |
| cost on **every** invocation | +135ms | +37ms | +63ms | 0 |
| new runtime dependencies | pydantic, pydantic-core | msgspec (C ext) | attrs, cattrs, exceptiongroup (3.10) | none |
| unknown keys — i.e. adapter options | preserved (`extra="allow"`) | **dropped** | **dropped** | n/a |
| `limit = true` | coerced to `1` | rejected | coerced to `1` | rejected today |
| `limit = "500"` | coerced to `500` | rejected | coerced to `500` | rejected today |
| error carries a path | yes | in the message | via `transform_error()` | ours to write |
| generates JSON Schema | yes | yes | **no** | ours to write |

Four things decide it, and none is the headline benchmark:

- **The cost is model *construction*, not import, and config is read on every invocation of
  both commands.** Pydantic builds a core schema per model at class-definition time, so its
  135ms lands on `hsql -c "select 1"` — 257ms today — taking it to roughly 390ms, past the
  300ms the product plan tracks in CI. There is no lazy way out, because the read path *is*
  the validating path. Pydantic is a no on the one axis it was proposed to improve.
- **Adapter options are unknown keys, and two of the three eat them.** A `Profile` model with
  declared fields turns `{"adapter": "postgres", "dbname": "warehouse"}` into a profile with
  no `dbname`, which is the value the adapter needed. msgspec and cattrs both drop unknown
  keys (their only alternative is to *reject* them), so under either, `profiles` stays
  `dict[str, dict[str, Any]]` with a model validating the keys core owns and the raw dict
  carried alongside. Only Pydantic's `extra="allow"` preserves them — the one place it is
  genuinely the better tool.
- **Two of the three would reintroduce a bug we already guard.** `limit = true` structures to
  `1` under Pydantic and cattrs. `parse_row_count()` rejects it today, with a comment saying
  why: a bool is an int in Python, and `true` is not one row. msgspec is strict and rejects
  it, which is the behavior we want and currently hand-write.
- **cattrs cannot generate the schema**, and `--config schema` is the deliverable that would
  most justify declaring a model at all. It is also the most expensive of the two viable
  options. Its advantages — pure Python, so no wheel per Python version, and a very stable
  ecosystem — do not pay for that here.

**The two-pass design changes the conclusion, and is the reason to take msgspec.** An earlier
draft of this section recommended staying hand-rolled, on the grounds that a model would
replace less code than it looked — the adapter-options tail would stay hand-validated
whatever we chose. §3.5's second pass is what dissolves that objection: those options are not
an untyped tail, they are a set the adapter *declares*, and a struct can be built from the
declaration at run time. Measured, `msgspec.defstruct()` for a twelve-option adapter costs
**0.022ms** and validating a profile against it **0.5µs**, so the per-adapter model is free
at the point of use.

What that buys is not a tidier version of validation we already do. It is validation of a
surface **nothing validates today** (§1.5) — the difference between `reed_only = true`
silently connecting read-write and an error naming the option and the adapter. It also comes
with the declared choices, so `sslmode = "verify-ful"` is caught by the same pass; and the
same declarations feed `--config schema`, so one source describes the config, validates it,
and documents it.

**So: msgspec, at +37ms on every invocation.** Strict where it matters (`limit = true` is
rejected rather than silently made `1`), path-carrying errors, JSON Schema included, Python
≥3.10 and cp310–cp314 wheels — exactly our floor and our matrix. The bet is that it is
pre-1.0 and effectively a one-maintainer project, which is the same class of bet M1
knowingly took on `tree-sitter`; if it goes bad, the fallback is the hand-rolled version of
the same two passes, which is a contained loss because the *design* is what matters here and
it survives the library.

What would reverse it: the 37ms mattering more than it looks once `--info` and `--spec` are
real, or wanting `extra="allow"`-style preservation badly enough to prefer Pydantic's cost.
Both are visible in PR 1, which is where this becomes real.

*Two questions an earlier draft left open are closed rather than answered.* `--describe`
does not exist, so neither does the question of whether it should report row counts. And
`--catalog` does not reuse the IDE's catalog cache: `catalog_cache.py` is kept for
compatibility and is not a live cache to warm or read (Ted). Repeat navigation re-resolves
its prefix, which is one round trip per segment and the price of not serving an agent a
stale catalog to write a query against.

---

## 7. Explicitly not in M2

**Cut in review, from the roadmap's own M2 list:**

- **`describe`.** `--catalog --path mydb.analytics.orders` already returns the columns of
  `orders` with their names, real types and quoted identifiers, in one round trip below the
  resolve — which is the whole of what a describe was going to print. The only thing a
  separate mode could add is per-object detail the adapter contract does not carry
  (comments) or that costs a table scan (row counts), and neither is worth a second mode
  that would otherwise be a synonym.
- **`hsql fmt`.** sqlfmt is already a dependency and the wrapper would be small, but the case
  for it is thin: an agent that wants formatted SQL can run `sqlfmt` itself, and this is the
  one mode that would have forced a 196ms import into a package whose import graph is
  otherwise a tested contract. Cut outright rather than deferred.
- **`--dry-run`.** In practice only DuckDB implements `validate_sql` at all, and even there
  it is a parse check rather than an existence check: measured on this checkout,
  `select * from no_such_table` and `insert into no_such_table values (1)` both validate
  clean, because the adapter deliberately treats every non-parser error as valid so that DDL
  passes. Making it useful would mean a `PREPARE` pass in the DuckDB adapter (which catches
  the missing table, but rejects DDL as a parser error) plus a refusal path everywhere else —
  a lot of machinery for a flag that would be honest on one adapter. Punted to a later
  milestone, or never.
- **Recursive `--depth`, the node budget, and child counts** (§1.3, §3.2). All three exist to
  make a recursive walk survivable; none is needed if the walk isn't there, and recursion
  should arrive with `fetch_descendants()` rather than ahead of it. Round-trip counting in
  `--stats` goes with them: it existed to make a walk's cost visible after the fact.
- **The `compact` and `tree` layouts** (§3.3). One level is a flat list, so `tree` has
  nothing to draw, and `compact`'s `parent(child TYPE, …)` shape only reads for a relation
  and its columns — what a level *is* belongs to the adapter (§1.2). The same rows through
  `-tA --format csv` are as compact and stay true at every level.
- **`--single-transaction`.** Adapters name their transaction modes freely and default to
  undefined transaction behavior, so core cannot drive a mode it can't identify; making it
  work would mean reworking the transaction-mode contract for a flag whose job a caller can
  do by putting `begin` and `commit` in the script they are already sending.
- **`--password-stdin`** (§3.7). Between profiles, `${VAR}` interpolation and the environment
  variables every driver already reads, it fills no gap worth a flag — and the one it does
  fill, it fills by consuming the stream `-f -` wants.

**Deferred to later milestones, as planned:** `hsql history` and `hsql open` (M4, with the
skill); `hsql mcp` (M6); streaming and pagination (M5); the interactive password prompt on
`harlequin` (#667's other half); the agent eval suite (dropped from M1); the site's
`llms.txt`, raw markdown routes and Docs API (M3). Publishing the JSON Schema at
`harlequin.sh/schemas/config/v1.json` is a `harlequin-web` PR that this repo's
`--config schema` feeds. Unifying the IDE's export dialog onto `write_file`, and making
`textual` an optional extra of `textual-fastdatatable`, both still deferred from M1.

---

## 8. Corrections to the product plan

Applied to `harlequin-for-agents.md` in the same PR as this document; recorded here so the
reasoning behind each is written down.

- **§4 and §6 spell the introspection surface as subcommands.** They are mode options on the
  one command — `hsql --catalog`, `hsql --info` — which matches `harlequin --config` and
  avoids ruling on whether `hsql catalog` means the verb or the database file of that name.
- **§6 says `hsql catalog mydb.analytics.orders` is "exactly one `fetch_children()` call."**
  It is one round trip *per path segment* plus one for the listing — four for that example —
  because an item can only be reached through its ancestors (§1.1).
- **§6's `--depth N` is a knob with a cliff behind it.** Its cost depends on the level it
  starts from: depth 2 from the root is one round trip per database, and depth 2 from a
  schema is 403 round trips and 2.3 seconds against a local file. M2 ships one level and no
  flag, and recursion returns with the adapter capability that would make it a single query.
- **§6's `describe` is `catalog` on a relation.** Listing one level below a relation is its
  columns, so the two would differ only in the per-object detail the contract doesn't carry.
  There is no `--describe`.
- **§6's child counts have no cheap case.** A count is only knowable by fetching the
  children, so the mitigation the product plan proposes cannot be implemented as stated; what
  a listing can report is what it fetched.
- **§6's `--format compact` is both unimplementable and ungeneralizable.** `CatalogItem`
  carries a 1–3 character `type_label`, so the type an agent needs is not in the contract at
  all — hence `type_name`, which the in-tree adapters already fetch and discard (§1.3). And
  the format's `parent(child TYPE, …)` shape only reads for a relation and its columns; a
  database's schemas are not a signature. There is no `compact` format; `-tA --format csv`
  over the same rows is as cheap and is true at every level.
- **§6's capability flags cannot be read off a connection reflectively.** SQLite defines
  `validate_sql` and raises `NotImplementedError` from it — which the app handles correctly
  today, but which no probe can distinguish from a real implementation. Declaring the flags
  on the adapter class is also what lets `hsql --info` answer while the database is
  unreachable (§1.4).
- **§5's `--timeout` needs `hsql` to attribute the cancellation itself.** A cancelled DuckDB
  query returns an empty result set and no error, so a timeout that trusts the adapter
  reports "no rows" and exits 0 (§1.9).
- **§5's `--dry-run`, `--single-transaction` and `--password-stdin`, and §6's `fmt`, are
  cut** (§7), so the roadmap's M2 row and §5's flag list both overstate what the milestone
  delivers. `--single-transaction` is the one worth a sentence in the product plan rather
  than only here: adapters name their transaction modes freely, so story A6's guarantee has
  to come from `begin`/`commit` in the caller's own script until the contract says which
  mode is which.
- **§7's config validation runs on the merged document**, so no error can name the file it
  came from. Validating each file as it is read, and merging only valid ones, is what makes
  the diagnostics §7 asks for possible (§3.5).
- **§7's config-file merge is per top-level key, and the failure is worse than the docs
  imply.** A cwd file that defines any profile replaces the home file's whole `profiles`
  table while leaving its `default_profile` behind, so both commands refuse to start (§1.6).
  Verified against the pre-refactor `config.py` as well: this is long-standing behavior, not
  something the `tomllib` change introduced.
- **§7's `${VAR}` interpolation needs to be a read-path transform**, because
  `harlequin --config` reads the config and writes it back — interpolating too early writes
  the resolved secret into the user's file (§1.6).
