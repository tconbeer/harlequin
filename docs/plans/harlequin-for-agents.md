# Harlequin for Agents

**A product plan for making Harlequin the best SQL client for coding agents.**

Status: draft for discussion. Product and user stories only — implementation details are
deliberately sketched, not specified.

Scope: the `harlequin` package (this repo) and the `harlequin-web` site
(<https://harlequin.sh>).

---

## 1. The opportunity

Today an agent asked to "look at my warehouse" reaches for whatever CLI is nearest:
`psql`, `duckdb`, `bq`, `databricks sql`, `mysql`, `trino`. Each has different flags,
different output formats, different auth, different error semantics, and different
ideas about what "give me the schema" means. The agent burns tokens rediscovering all
of that on every task, and it hallucinates flags for the ones it has seen least.

Harlequin already has the thing that fixes this, and nobody else does:

- **One uniform adapter interface across ~15 databases** — DuckDB, SQLite, Postgres,
  MySQL, BigQuery, Trino, Databricks, ODBC, ADBC, Cassandra, NebulaGraph, RisingWave,
  Wherobots, Exasol.
- **A normalized catalog abstraction** (`Catalog` / `CatalogItem`) that already answers
  "what tables exist and what are their column types" without dialect-specific
  `information_schema` queries.
- **Profiles in a config file** that already encode "how to connect to my warehouse,"
  discovered automatically from the cwd and home directory.
- **sqlfmt** as an existing dependency.
- **A TUI on the other end of the same config**, which no other CLI has.

The pitch writes itself: **`hsql -P prod -c "select 1"` works the same way against every
database your team uses.** One CLI contract, one set of flags, one output format
vocabulary, one exit-code scheme — and when the agent needs a human, the human opens
Harlequin against the same profile and sees the same catalog.

That last point is the real differentiator. Every other SQL client can be made
agent-friendly. Only Harlequin can make the *handoff* good.

## 2. Who we are designing for

Three personas, all of which are "an agent," but with materially different needs.

### P1 — Agent as operator

Runs queries headlessly on a human's behalf, usually in a loop, usually inside a coding
agent's Bash tool. Cares about: deterministic output, honest truncation, exit codes,
token-efficient results, not accidentally dropping a table.

### P2 — Agent as pair

Works alongside a human who has Harlequin open. Drafts a query the human will run;
reads what the human already ran. Cares about: handoff in both directions.

### P3 — Agent as support engineer

Helps a human install, configure, and troubleshoot Harlequin — "why won't it connect to
Snowflake," "add a prod profile to my config," "what does this error mean." Cares about:
machine-readable docs, a formal config spec, a self-describing CLI, good diagnostics.

Most existing "AI-friendly CLI" work only serves P1. Serving P2 and P3 is where
Harlequin can be uniquely good.

## 3. Design principles

These are the calls I'd want made up front, because they resolve most of the small
decisions downstream.

1. **One engine, two front doors.** `harlequin` (TUI) and `hsql` (headless) share
   adapters, config discovery, profiles, limit semantics, and one execution core.
   Neither reimplements the other. See §4.

2. **stdout is data; stderr is narration.** Results go to stdout and nothing else does.
   Truncation notices, warnings, errors, and `--stats` go to stderr — and nothing that
   stdout already carries: the row count is the result's own footer, and a timing is a
   field of `--stats`. This is already the intent in `exception.py` — make it a hard,
   tested contract.

3. **Exit codes are an API.** Documented, stable, and distinct enough that an agent can
   branch on them without parsing text.

4. **Determinism over cleverness: identical output whether stdout is a TTY, a pipe, or
   a pty.** Agent harnesses vary in which they give you. A client that formats
   differently under a pipe is a bug factory. No TTY-sniffing for format selection.
   (Color is the one exception, and it follows `NO_COLOR` / `--color`.)

5. **Token efficiency is a feature.** Results cost the agent money. Defaults should be
   small, and truncation must always be announced — never silent.

6. **Be boring and psql-shaped.** An agent's prior is `psql`, secondarily `duckdb`.
   Every flag we can borrow, we borrow. Novel spellings cost tokens and cause retries.

7. **Never make the agent guess.** Everything Harlequin knows about itself — options
   (including plugin-contributed ones), profiles, formats, adapters, capabilities,
   catalog — should be dumpable as JSON on demand. This is the one place we should be
   *more* capable than psql, because our option surface is dynamic and plugin-driven, so
   no amount of pretraining can teach an agent what's actually installed.

8. **Safety is a product feature, not a warning in the docs.** `--read-only`,
   `--timeout`, and `--dry-run` are what let a human hand an agent a warehouse
   credential without flinching.

9. **No LLM inside Harlequin.** No API keys, no model config, no vendor coupling. We
   make Harlequin excellent for the agent already sitting next to the user. (See §10 for
   how this answers issue #952.)

---

## 4. Shape of the CLI: two commands, one engine

**Decision: headless mode ships as a separate command, `hsql`, not as flags on
`harlequin`.**

The tempting reason — it frees the flag namespace — is the weakest one. The real reasons
are these.

**Two audiences want two stability contracts.** The TUI's promise is "delight a human,
evolve freely." The headless promise is "output format and exit codes are an API,
frozen." Inside one command those pull against each other on every release, and a TUI
change can silently break a machine contract. A separate command makes the boundary
structural rather than something a reviewer has to remember.

**Cold start stops being a discipline problem.** With one entry point, "never import
Textual on the headless path" is a rule any contributor can regress silently. With a
separate module graph it becomes an import-linter test in CI — *the `hsql` module graph
must not contain `textual`* — which is the difference between hitting the 300ms target
and hoping to.

**It opens a lean install later.** `pip install hsql` with no Textual, no pyperclip, is
plausible for CI images and agent sandboxes, and hard to reach from a single entry point.

### What actually ships

**The same package, with a second console script:**
`hsql = "harlequin.hsql:main"` alongside the existing `harlequin` script.

That is the key de-risking move. **The command split is cheap and near-reversible; the
package split is the expensive part, and it can wait indefinitely** — possibly forever.
We get the boundary and the enforceable import graph now, and decide about a separate
distribution only if the lean-install demand materializes.

`harlequin` itself gains **nothing**: no `-c`, no growth in an already-large `--help`,
no behavior change. The one addition is a custom unknown-option handler, so
`harlequin -c "select 1"` fails with *"`-c` isn't a harlequin option — did you mean
`hsql -c`? See harlequin.sh/docs/headless."*

Everything in Workstream B lands as `hsql` subcommands: `hsql catalog`,
`hsql describe`, `hsql fmt`, `hsql spec`, `hsql info`, `hsql config`, `hsql history`,
`hsql mcp`, `hsql open`. Bare `hsql` with no arguments prints help; it never launches
the TUI.

> **Amended in M2's plan: these are mode options, not subcommands.** `hsql --catalog`,
> `hsql --info`, `hsql --spec`, `hsql --config show`. It matches `harlequin --config`, so
> one shape covers both commands — and it sidesteps the objection this section raises
> against subcommands, which applies here too: `hsql` takes `CONN_STR` positionally, so
> `hsql catalog` and a database file named `catalog` would have needed a disambiguation
> rule, docs for the rule, and a test for the rule. No verb, no rule.

### Consequences

- **The `-f` collision disappears, and with it the plan's only breaking change.**
  `harlequin -f` stays `--show-files` forever; `hsql -f` is `--file`, as psql users
  expect. Nothing here needs to wait for 3.0 — the whole plan becomes additive and can
  ship in 2.x minors.
- `hsql` gets clean access to `-c`, `-f`, `-o`, `-A`, and friends, so psql muscle memory
  works without compromise.
- `hsql --help` omits every TUI-only option (`--theme`, `--keymap-name`, `--show-files`,
  `--show-s3`, `--locale`, `--config`, `--keys`), which is a real token saving for an
  agent reading it. It also omits the adapter connection-option matrix: `hsql --help`
  lists the *names* of installed adapters and points at `hsql --help -a <adapter>` for
  one adapter's options. That keeps the first thing an agent reads small and stable, and
  keeps `--help` true for every adapter rather than showing whichever one is the
  default.
- The handoff gets a natural verb: `hsql open query.sql` reads much better than
  `harlequin --new-buffer query.sql`.

### Alternatives rejected

- **Flags on `harlequin`.** Requires the 3.0 `-f` break, grows an already-large help
  surface, and leaves cold start and the stability boundary as matters of discipline.
- **Subcommands on `harlequin` with a bare-invocation TUI fallback**
  (`harlequin query -c ...`). Needs a custom Click `Group` that falls back to the TUI
  when the first argument isn't a known subcommand, and it is genuinely ambiguous: a
  DuckDB file named `catalog` or `config` in the cwd resolves as a subcommand. Low
  probability, but exactly the confusing failure we don't want an agent hitting.

### On the name

`hsql` is free on PyPI as of this writing — M0 in §11 secures it before anything else
starts. HSQLDB ships `sqltool` and `java -jar`, not a binary named `hsql`, so there's no
PATH collision — the cost is search-results mindshare with
HyperSQL, which is real but mild and fading. `hq` is taken. `harlequin-cli` is free but
too long for something an agent types a hundred times in a task; short names are cheap
in tokens and cheap in working memory.

I'd take `hsql`. It's obviously "harlequin sql," and it sharpens the positioning rather
than diluting it:

> **Harlequin** — the SQL IDE for your terminal.
> **hsql** — the same engine, headless, for scripts and agents.
> Fifteen databases, one adapter ecosystem.

That's a better story than "one tool with a flag."

---

## 5. Workstream A — `hsql`, headless execution

Closes [#524](https://github.com/tconbeer/harlequin/issues/524). This is the
foundation; everything else in this plan assumes it.

### Proposed surface

```
hsql [OPTIONS] [CONN_STR]...        Execute SQL and exit
hsql <SUBCOMMAND> [OPTIONS]         catalog, describe, fmt, spec, info,
                                    config, history, open, mcp

  -c, --command TEXT     Execute SQL. Repeatable.
  -f, --file PATH        Execute SQL from a file (or `-` for stdin). Repeatable.
  -o, --output PATH      Write results to PATH instead of stdout.
      --format NAME      Output format (see below). Default: table.
      --csv/--json/--jsonl/--markdown/--vertical    Format shorthands.
  -t, --tuples-only      Rows only: no header, no footer. As in psql.
  -A, --no-align         Unaligned output. As in psql.
      --no-header        Omit the header row (keep other chrome).
      --null-string STR  Render NULL as STR. Default: empty for csv, "NULL" for table.
  -P, --profile NAME     Same profiles as the TUI.
      --limit N          Max rows fetched per result set. Default: 500. -1 = no limit.
      --result all|last|N  Which result set(s) to emit. Default: all.
      --on-error stop|continue    Default: stop.
      --stats            Write a one-line JSON summary to stderr.
      --color auto|always|never   Default: never.
      --timeout SECONDS  Cancel the query and exit non-zero.
      --read-only        Refuse to run if the adapter can't enforce read-only.
      --dry-run          Parse/validate (and EXPLAIN where supported) without executing.
      --single-transaction        Wrap the whole script; roll back on any error.
```

Config discovery, profiles, and precedence are identical to the TUI: CLI > env >
`--config-path` > cwd config > user config dir > home > defaults. That's the whole
point — `hsql -P prod -c ...` means the agent never handles a credential.

### Formats

| Format | Use |
| --- | --- |
| `table` (default) | Aligned text. Human-legible, deterministic. |
| `markdown` / `md` | Pipe table. The most reliable format for an LLM to read back. |
| `csv`, `tsv` | Pipelines, spreadsheets. |
| `json` | Array of row objects. Matches `duckdb -json`. |
| `jsonl` / `ndjson` | One object per line. Streaming, large results. |
| `vertical` | psql `\x`. One column per line — best for inspecting a single wide row. |
| `parquet`, `arrow`, `orc` | Bulk handoff. Already implemented in `export.py`. |
| `none` | Discard rows; report status only. For DDL/DML/ETL. |

> **Amended in implementation.** There is no `-F`: that is psql's
> `--field-separator`, and a flag that sets a delimiter in one command and picks a
> format in the other is a mistake waiting for a script — the same reasoning that
> keeps `-l` off `--limit`. The format is chosen by `--format` spelled out, or by
> the `--csv`/`--json`/`--jsonl`/`--markdown`/`--vertical` shorthands, which cover
> the choices anyone types often enough to want a short flag for.

### Behaviors that matter more than the flag list

**Truncation is always announced.** If `--limit` bites, stderr gets
`note: results truncated at --limit 500; pass --limit -1 for all rows`, and text formats
append a visible `… 500 of >500 rows` footer.

Note that `--limit` here is a *hard* limit — it caps what leaves the database, via the
adapter's `set_limit()`. The TUI's `--limit` is a soft display cap: it fetches everything
and caps what loads into the viewer, which is why the TUI can report an exact
`Showing 100,000 of 3,412,887`. `hsql` deliberately doesn't buy that number, so it says
`>500`; only `--limit -1` yields an exact count. The TUI's default of 100,000 is
right for a TUI and a catastrophe for an agent; `hsql` defaults to 500.

> **Amended by [#1026](https://github.com/tconbeer/harlequin/issues/1026).** One key
> naming both of those limits was the bug, not the design: the TUI's display cap is now
> `viewer_max_rows`, `limit` is the hard fetch limit in both commands, and unlimited is
> spelled `-1` rather than `0`, so that `--limit 0` can fetch a header and no rows (and
> there is no `-l`, which is psql's `--list`). The text
> layouts additionally cap what they *print* (`--display-rows`, 40 rows or 10 records),
> since fetching 500 rows and printing 500 rows are separate questions.

Detecting truncation at all requires fetching `limit + 1` rows and emitting `limit` —
`set_limit(n)` followed by `fetchall()` returns at most n rows and cannot say whether an
n+1th existed. Worth stating here rather than leaving to implementation, because it's the
kind of detail that gets optimized away and quietly breaks this promise.

The stderr notice fires **even under `-t`**. `-t` suppresses stdout chrome, not
warnings; a flag that silently defeats truncation reporting would undo principle 5.

**Row counts and timing go to stderr**, so `hsql -c "select 1" --csv > out.csv` produces
a clean file while the agent still sees `1 row in 0.02s`.

**`--stats` emits one line of JSON to stderr** for any output format:

```json
{"status":"ok","statements":1,"rows":500,"truncated":true,"limit":500,
 "elapsed_ms":412,"columns":[{"name":"id","type":"BIGINT"}]}
```

This is how an agent gets structured metadata without polluting stdout, and it works
identically whether stdout is CSV or Parquet.

**Errors are plain.** No Rich panels, no ANSI, no box drawing:
`hsql: error: relation "usres" does not exist` on stderr. The panel is a TUI affordance.

> **Amended in M2's plan**, on two of the safety flags above.
>
> **`--timeout` has to attribute the cancellation itself.** A cancelled DuckDB query comes
> back as an empty result set with no error — `fetchall()` swallows the interrupt and
> returns `None`, which is also what an empty result looks like — so a timeout that trusts
> the adapter prints "no rows" and exits 0. `hsql` knows it fired the deadline, discards
> that result, and exits 4. It also refuses up front on an adapter that can't cancel,
> since there is otherwise no way to stop the work.
>
> **`--single-transaction` is cut too.** Adapters name their transaction modes freely —
> SQLite and Postgres offer "Auto"/"Manual", DuckDB overrides nothing and reports none, and
> a third-party adapter may name its modes anything and default to whatever its driver
> does — so core cannot ask for "the manual one" through that interface without first
> reworking what a transaction mode is. Story A6's guarantee comes from `begin` and
> `commit` in the caller's own script until then.
>
> **`--dry-run` is cut**, from M2 and possibly for good. Only DuckDB implements
> `validate_sql` at all, and there it is a parse check rather than an existence check:
> `select * from no_such_table` validates clean, because the adapter treats every
> non-parser error as valid so that DDL passes. Making it honest would mean a `PREPARE`
> pass in the DuckDB adapter plus a refusal path on every other adapter — a lot of
> machinery for a flag that would be truthful on one backend.

**Exit codes:**

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Query error (the database rejected the SQL) |
| 2 | Usage / config error (bad flag, bad profile, bad TOML) |
| 3 | Connection error (couldn't reach or authenticate to the database) |
| 4 | Timeout / cancelled |
| 130 | SIGINT |

**Cold start is a feature.** An agent may run twenty queries in a task. Target:
**`hsql -c "select 1"` against DuckDB in under 300ms**, tracked as a benchmark in CI and
protected by the import-linter rule from §4.

**Large results stream — in M5, not M1.** Streaming has to start at the cursor:
`HarlequinCursor.fetchall()` materializes the whole result before any writer exists, so
no amount of care in the format layer helps. Deferred wholesale to M5 along with
[#875](https://github.com/tconbeer/harlequin/issues/875), rather than half-designed
now.

### User stories

- **A1.** As an agent, I run `hsql -P prod -c "select count(*) from orders"` and get a
  small, aligned result on stdout and exit code 0, so I can report a number to the human
  without parsing a TUI.
- **A2.** As an agent, I run the same command against Postgres, BigQuery, and DuckDB and
  the flags, output shape, and exit codes are identical, so I don't relearn a CLI per
  database.
- **A3.** As an agent, when my SQL is wrong I get a one-line error on stderr and exit
  code 1, and nothing on stdout, so I can distinguish "the query failed" from "the query
  returned zero rows."
- **A4.** As an agent, when a result is larger than the limit I am told so explicitly, so
  I never report a truncated aggregate as a complete one.
- **A5.** As an agent, I pass `--json` and pipe to `jq` without stripping banners,
  timings, or a "(500 rows)" footer.
- **A6.** As an agent, I run a multi-statement script with `-f setup.sql
  --single-transaction` and either all of it applies or none of it does.
- **A7.** As a human, I set `read_only = true` in my agent profile so the agent
  physically cannot write to prod, and I can point at that line in the config when
  someone asks whether it's safe.
- **A8.** As a human, I use `hsql` in a Makefile or CI job because it's the same engine
  and the same profile I already use interactively.

### On `-t`, and the `-tAc` idiom

`-t` is `--theme` in `harlequin` and "tuples only" in psql. Since `hsql` has no themes,
`-t` is free, and it takes the **psql meaning**.

The reason isn't `-t` in isolation — it's that `psql -tAc "select count(*)"` is a single
well-worn idiom, the standard way to capture a scalar in a shell script. Supporting `-t`
and `-A` separately but not together would be pointless; refusing `-t` would break the
cluster and make first contact with a scripting audience an error message. Borrowing the
flag is principle 6 doing its job.

The residual risk is small and worth naming: someone with `harlequin -t nord` habits
types `hsql -t nord -c ...`, `-t` parses as a boolean, and `nord` is read as a
connection string. The resulting "could not open database 'nord'" is diagnosable, and we
can special-case the hint when an unparseable conn_str matches a known theme name.

`hsql -tAc "select count(*) from orders"` returning a bare number and nothing else
should be an explicitly tested case, and the first example in the scripting docs.

A short "differences from psql" table in the docs should still cover `-P` (profile here,
pset there) and anything else that diverges.

---

## 6. Workstream B — Self-description and introspection

If Workstream A is what lets an agent *run* a query, this is what lets it write a
correct one on the first try. All of it lands as `hsql` subcommands.

### `hsql catalog` — the catalog is a filesystem, not a document

The highest-leverage feature in this plan after `-c`. Harlequin already normalizes the
catalog across every adapter; today that value is locked inside the TUI.

**The binding constraint is fetch cost, not token cost.** `get_catalog()` issues one
query and returns databases only; every level below it is a separate `fetch_children()`
round trip, with columns as the leaf. Listing one schema's columns is `1 + 1 + 1 + T`
queries. A "dump the tree, then filter" interface is an N+1 walk that gets slower the
more useful it would be — and #1007 already moved the TUI off eager loading for exactly
this reason. Filtering in the client protects the agent's context and does nothing for
the database.

So the model is a filesystem: **`ls`, `stat`, `find`** — not dump-and-grep. That bounds
cost structurally, because you only pay for the level you asked for, and it rides on the
navigation loop agents are most practiced at.

```
hsql catalog [PATH] [--depth N] [--max-nodes N] [--format compact|json|md|tree]
hsql describe <PATH>
hsql find <TERM> [--in tables|columns|all]
```

- **One level below the match, by default.** `hsql catalog` → databases;
  `hsql catalog mydb` → schemas; `hsql catalog mydb.analytics` → relations;
  `hsql catalog mydb.analytics.orders` → columns. Exactly one `fetch_children()` call.
  `--depth N` opts into recursion; there is no unbounded default.

  Counting depth **from the match** rather than from the root is what makes this safe
  rather than merely conservative: at every level, depth 1 is a single round trip, so
  there is no path on which the default degrades into a walk. Depth ≥2 from a schema is
  the first N+1, and it's opt-in.

- **Patterns are paths with an optional trailing glob.** A trailing wildcard filters one
  resolved parent's children and stays a single round trip, so `analytics.*` is just
  sugar for `analytics`, and `analytics.ord*` narrows it. An *interior* wildcard
  (`*.orders`, `mydb.*.orders`) can't be evaluated without fetching every candidate
  level, so it belongs to `find` below — never something a `catalog` call does quietly.
- **Report child counts** wherever the adapter can get them cheaply. `400 relations` next
  to a schema is what lets an agent decide *not* to recurse. Cost should be predictable
  before the call, not just measurable after it.
- **A node budget.** `--max-nodes` (default ~2000) stops a recursive walk and says so,
  naming the path to narrow. Same rule as row truncation: never silent.
- **`--stats` reports round trips**, so the expensive shape of a query is visible.
- **Emit `query_name`, not just the label.** Adapters already compute the correctly
  quoted identifier for every item. Handing it over means the agent never guesses whether
  this backend wants `"Orders"` or `` `orders` ``. It costs nothing and no other client
  does it.
- **`--format compact`** stays for the leaf and shallow-recursion cases:
  `analytics.orders(id BIGINT, customer_id BIGINT, total DECIMAL(18,2))`. Roughly an
  order of magnitude cheaper than pretty JSON and perfectly readable by a model.
- **`describe`** returns one object in full — columns, types, comments, row counts where
  the adapter can supply them. One round trip.

> **Amended in M2's plan**, on four things this section assumes the catalog contract can
> already do.
>
> **A listing is one round trip per path segment, plus one** — four for
> `--catalog --path mydb.analytics.orders`, not one. An item can only be reached through
> its ancestors, so the resolve in front of the listing is itself a walk. The listing stays
> single-level and cheap; the resolve is what a network database charges for.
>
> **`--depth N` is one number over levels with wildly different costs, so M2 ships no
> recursion at all.** Depth 2 from the root fetches each database's schema names — one
> round trip per database, and databases are few. Depth 2 from a *schema* is one round trip
> per relation: measured, 400 relations is **403 round trips and 2.3 seconds against a
> local DuckDB file**, on the call an agent would most want to make. Recursion comes back
> with `fetch_descendants()`, which is what would make it a single query rather than a
> walk with a budget bolted on.
>
> **Child counts have no cheap case.** A count is only knowable by fetching the children,
> so "report child counts wherever the adapter can get them cheaply" cannot be implemented
> as written. What a listing can report is what it fetched, which it already does.
>
> **`describe` is `catalog` on a relation, so there is no separate verb.** One level below
> a relation *is* its columns, with their names, real types and quoted identifiers. All a
> second mode could add is detail the contract does not carry (comments) or that costs a
> table scan (row counts).
>
> **`--format compact` is cut, and the type it wanted is added.** `CatalogItem.type_label`
> is a 1–3 character label for a tree column (`##`, `s`, `ts`), so the type an agent needs
> is not in the contract at all — M2 adds an optional `type_name`, which the in-tree
> adapters already fetch and discard, falling back to the short label where an adapter
> never populates it. The *format* goes: `parent(child TYPE, …)` only reads for a relation
> and its columns, and what a level is belongs to the adapter, so a database's schemas
> would render as a signature they are not. `-tA --format csv` over the same rows is as
> cheap and is true at every level.

### `hsql find` — the real gap

What an agent most often needs isn't "list this schema," it's *"where does `orders`
live"* or *"which tables have a `customer_id`."* That cannot be a walk.

This wants an **optional adapter capability** (`implements_catalog_search`): one
`information_schema`-style query where the adapter can serve it, and an explicit "not
supported by this adapter" where it can't, surfaced through the capability flags in
`hsql info`. High value, but it should not block the rest of the workstream.

### An adapter-API question this raises

`fetch_children` is per-item, which is exactly right for a viewport-driven TUI and forces
N+1 for bulk access. An adapter that could pull an entire schema's columns in a single
`information_schema` query has no way to say so.

Worth considering an optional **`fetch_descendants(depth)`** on `InteractiveCatalogItem`,
with a default implementation that simply walks `fetch_children`. Adapters that can do
better override it; everything keeps working for those that don't. This is a
Harlequin-core API decision, not an `hsql` one, and it would improve the TUI's
expand-all path too.

**Stories:**

- **B1.** As an agent, I run `hsql -P prod catalog` and then `hsql -P prod catalog mydb`
  to orient myself, paying one query per step and knowing what each step cost.
- **B2.** As an agent, I see `analytics — 400 relations` and scope my next call instead of
  recursing into a walk that would take a minute and blow my context.
- **B3.** As an agent, I run `hsql -P prod catalog mydb.analytics.orders --format compact`
  and write a correct join on the first attempt, pasting the adapter's own quoted
  identifiers rather than guessing at them.
- **B4.** As an agent handed an unfamiliar warehouse, I run `hsql find orders` and get a
  path instead of walking three levels to look for one.

### `hsql spec --json`

Dump the full, *installed* CLI surface — including options contributed by whichever
adapter plugins are present — as JSON. Harlequin already builds these dynamically from
`AbstractOption`, so the data exists; it just isn't reachable.

This is unusually valuable here precisely because the option surface is plugin-driven.
No model's pretraining can know that this machine has `harlequin-databricks` installed
with its particular options. Self-description turns a guessing game into a lookup.

- **B4.** As an agent, I run `hsql spec --json` once at the start of a task and never
  hallucinate a flag for the rest of it.

### `hsql info --json`

The debug-info screen, headless: versions, installed adapters, discovered config files in
precedence order, the active profile, and **capability flags per adapter**
(`implements_cancel`, `implements_copy`, `implements_read_only`,
`implements_validate_sql`, `supports_transactions`). Credentials and connection strings
redacted.

Capability flags matter: they let an agent know *not* to try `--dry-run` on an adapter
that can't validate, instead of trying and failing.

> **Amended in M2's plan.** The flags have to be *declared* on the adapter class, not
> detected on the connection. Reflection can't answer the question:
> `HarlequinSqliteConnection` defines `validate_sql` and raises `NotImplementedError` from
> it, so an override check reports SQLite as validating SQL. Nothing is broken by that
> today — the app catches the error at the call site — but a class variable is the pattern
> that can be read *before* the call, and the only one `hsql --info` can read while the
> database is unreachable, which is when a diagnostic is worth most. So `--info` imports
> adapters and opens no connection.

- **B5.** As an agent debugging a human's setup, I run `hsql info --json` and can tell
  them exactly which config file is winning and why.
- **B6.** As an agent, I check capability flags before offering the human a feature their
  adapter doesn't support.

### `hsql fmt`

Expose sqlfmt, which is already a dependency. `hsql fmt query.sql`,
`hsql fmt -c "select 1"`, `--check` for CI. Agents write inconsistent SQL; this
normalizes it before it lands in a human's repo. Cheap, and a genuinely nice human
feature too.

> **Cut in M2's plan.** An agent that wants formatted SQL can run `sqlfmt`, which is what
> this would have wrapped. It is also the only headless mode that would have forced a
> 196ms sqlfmt import into a package whose import graph is otherwise an enforced contract —
> paid by the mode alone, but a deferral to maintain forever for a wrapper nobody asked
> for.

### `hsql history`

Query history is already cached per connection — but as a pickle. Expose it
(`hsql history --json -n 20`) and additionally write an append-only JSONL log, so both
agents and humans can read it with ordinary tools.

- **B7.** As an agent joining a task mid-stream, I read the human's recent query history
  and continue their line of investigation instead of starting over.

---

## 7. Workstream C — The config file as a formal contract

Agents write config files. Today they'd be guessing at TOML structure from prose docs,
and a wrong guess produces a confusing runtime error. The config is shared between
`harlequin` and `hsql`, so this serves both.

### Deliverables

1. **A published JSON Schema** for `.harlequin.toml`, at
   `https://harlequin.sh/schemas/config/v1.json` and shipped in the package. Enables the
   `#:schema` comment for taplo / Even Better TOML — editor validation for humans, and a
   verifiable target for agents.
   - Wrinkle: adapter options are dynamic. Solution: publish a base schema for the
     site, and have `hsql config schema --json` generate a locally-accurate one that
     includes the installed adapters' options.
2. **`hsql config validate [--json]`** — structured diagnostics with file paths and line
   numbers, so an agent can fix its own mistake without a human in the loop. The existing
   `_raise_on_bad_schema` messages are already good; they just need a machine channel.
3. **`hsql config show [--json]`** — the effective merged config, with provenance per key
   (`limit = 500  # from ~/.harlequin.toml`). This is the single best troubleshooting
   artifact we could produce.
4. **`hsql config init --non-interactive`** — the TUI wizard (`harlequin --config`) is
   questionary-only, so agents can't use it. A non-interactive path that edits TOML
   through tomlkit (preserving the human's comments and formatting) means an agent can
   safely modify a config it didn't write. The interactive wizard stays exactly where it
   is; each command gets the affordance right for its audience.
5. **Environment variable interpolation** (`${DB_PASSWORD}`) in config values — closes
   [#898](https://github.com/tconbeer/harlequin/issues/898), and it's a prerequisite for
   agents and CI ever touching a config that involves secrets.
6. **A formal spec page on the site**: discovery order, merge semantics, precedence,
   every key with its type and default, and the reserved/invalid names.

> **Amended in M2's plan**, on two things this workstream would otherwise document as they
> stand.
>
> **The merge is per top-level key, not per profile, and the result is a start-up
> failure.** `_merge_config_files()` is a `dict.update()` per file, so a cwd config file
> that defines any profile replaces the home file's whole `profiles` table — while leaving
> its `default_profile` in place, since that is a different top-level key. Both commands
> then refuse to start: *"Config files set the default_profile to personal, but do not
> define a profile with that name."* Reproduced on this checkout, and against the
> pre-refactor `config.py` as well, so it is long-standing rather than something the
> `tomllib` change introduced. M2 makes the merge per-key and treats it as a bug fix; the
> spec page should describe that rather than what exists today.
>
> **Validation runs on the merged document, so no error can name a file.** `load_config()`
> merges every discovered file and then validates the result, which means a diagnostic can
> only ever point at a key in a structure nobody wrote. M2 validates each file as it is
> read and merges only valid ones, which is what makes deliverable 2's file-and-line
> diagnostics possible. It also adds `hsql --config list-profiles`, since the first
> question a human asks is what they can pass to `-P`, and a missing name is how the merge
> bug above becomes visible.
>
> **`${VAR}` interpolation has to be a read-path transform.** `harlequin --config` reads
> the config and writes it back, so resolving values too early bakes a plaintext password
> into the user's file on their next wizard run. The syntax is `${VAR}` and
> `${VAR:-default}` — a bare `{` never triggers it, so nothing anyone has in a password
> today needs escaping — and an unset variable with no default is a config error naming
> the variable and its file, never an empty string.

### Secrets: #667, #898, and redaction are one feature

[#667](https://github.com/tconbeer/harlequin/issues/667) asks for a password prompt, and
the maintainer's read at the time was that it wants **a new type of adapter option**.
That's the right shape, and it does far more than serve the prompt: it turns redaction
from a discipline into a property of the declaration.

The decisive argument is the plugin ecosystem. Core cannot enumerate every adapter's
secret — `--service-account-key`, `--token`, `--tls-key`, whatever the next adapter
invents. But each adapter can declare its own, once, and then every consumer gets it
free: `hsql info --json`, `config show`, `spec --json`, error output, the debug-info
screen, and the config wizard's masked input.

Combine that with `${VAR}` interpolation (#898) and the secrets story closes:

- **`PasswordOption`** (or `secret=True` on `AbstractOption`) — declares an option as
  sensitive. Drives `hide_input` at the prompt, a masked widget in the TUI, and
  redaction everywhere else.
- **Never prompt in headless mode.** A prompt that blocks on stdin is the *worst*
  failure mode for an agent: no output, no exit code, the whole turn burned until
  something times out. `hsql` must fail fast with a message pointing at the profile or the
  env var.

  > **Amended in M2's plan: no `--password-stdin` either.** Between profiles, `${VAR}`
  > interpolation and the variables every driver already reads, it fills no gap worth a
  > flag — and the gap it does fill, it fills by consuming the stream `-f -` wants.
- **`spec --json` marks secret options.** This is what teaches an agent *not* to
  construct `hsql --password hunter2`, which would leak through `ps` and shell history
  to every other user on the box. The skill should say it too, but the machine-readable
  flag is what makes it checkable.
- **Scrub known secret values from outbound strings** as a backstop. Declarative
  redaction covers values we own; a driver exception that echoes a DSN does not go
  through our option layer.
- **Connection strings need their own handling.** `conn_str` is a positional argument, so
  no option type covers `postgres://user:pw@host/db`. It needs DSN-aware redaction of its
  own — and [#354](https://github.com/tconbeer/harlequin/issues/354) is evidence that
  people really do put passwords there.
- **History is the one surface this can't fix.** `create user x password 'y'` is a secret
  inside a query. Worth saying plainly in the docs that history is local-only and may
  contain secrets by construction.

### Stories

- **C0.** As a human, I hand my agent a profile name, and no secret ever appears in an
  argument list, a transcript, or `ps` output.
- **C1.** As an agent, I add a `prod` profile to a human's existing config without
  clobbering their comments or reformatting the file.
- **C2.** As an agent, I validate the config I just wrote and get a line number when I
  get it wrong.
- **C3.** As a human, my editor red-squiggles a typo'd key before I ever run Harlequin.
- **C4.** As an agent, I write a config that references `${SNOWFLAKE_PASSWORD}` and never
  see the secret itself.

---

## 8. Workstream D — Docs that machines can read (harlequin.sh)

The docs are already markdown files on disk (`src/docs/**/*.md`) and there's already an
`/api/docs` index route. Most of this is plumbing we half-have.

### D1. `llms.txt` and `llms-full.txt`

Highest ROI item on the site. `llms.txt` at the root: an index of every docs page with a
one-line description and a link. `llms-full.txt`: the whole documentation set
concatenated as clean markdown. Both prerendered at build time. An agent fetches one URL
and knows everything.

### D2. Raw markdown per page

`https://harlequin.sh/docs/getting-started/running.md` returns `text/markdown`.
Implemented as a catch-all `+server.ts` reading the source via `import.meta.glob(...,
{ query: '?raw' })`.

The real work is sanitization: the sources carry mdsvex `<script>` blocks and Svelte
component tags (`<Tip>`, `<Figure>`, `<Key>`). The raw route must strip the script
blocks and downgrade components to plain markdown equivalents (`<Tip>` → a blockquote,
`<Figure>` → an image with the caption as alt text, `<Key>` → backticks). Worth
budgeting real time for; a raw route that emits Svelte tags is worse than none.

### D3. Docs API v1

Extend the existing endpoint into something documented and stable:

- `GET /api/docs` → index (exists today)
- `GET /api/docs/{topic}/{page}` → `{ title, topic, slug, markdown, updated_at }`
- `GET /api/docs/search?q=` → keyword search over a prebuilt index (the corpus is small;
  no vector store needed)
- CORS `*`, cached with the ISR config already in use.

### D4. Copy / view as markdown

A "Copy page as Markdown" button and a "View as Markdown" link in the docs layout. The
copy variant should prepend the canonical URL and title as a header so pasted context
carries its provenance. Small, cheap, and increasingly expected.

### D5. A "Headless & Agents" docs topic

A first-class topic in the sidebar, not a footnote. Pages:

- The `hsql` CLI — overview and tutorial
- Output Formats — the table from §5, with examples
- Exit Codes and Error Handling
- Catalog and Introspection
- Safety: read-only, timeouts, dry runs
- Differences from `psql`
- Config File Spec (links to Workstream C)
- Using Harlequin with Claude Code and other agents — the skill
- The MCP server

The topic is named for the capability rather than the binary, so a human who doesn't yet
know `hsql` exists can still find it.

### D6. Homepage positioning

The two-command story from §4 needs a home on the landing page — a short section that
says Harlequin is the IDE, `hsql` is the same engine headless, and both speak fifteen
databases. This is the page a human sends to their agent, which makes it the entry point
for P3.

### D7. `AGENTS.md` in the repos

For agents working *on* Harlequin: repo layout, how adapters plug in, how to run tests
(`make check`), the snapshot-test workflow, and the changelog convention. Add it to this
repo and to `harlequin-adapter-template` so new adapters inherit it.

### Stories

- **D1s.** As an agent helping a human install Harlequin on Windows, I fetch
  `llms-full.txt` and answer from current docs instead of stale pretraining.
- **D2s.** As an agent, I fetch one page as markdown and spend a tenth of the tokens I'd
  spend on the rendered HTML.
- **D3s.** As a human, I click "Copy as Markdown" and paste an accurate page into my
  agent's context.

---

## 9. Workstream E — Integrations: skill first, MCP last

### The recommendation, and why

**Ship a skill first. Make MCP a thin wrapper over the same execution core, not a
parallel implementation.**

A good CLI plus a skill — one markdown file that teaches `hsql` — works in *any* agent
harness that can run a shell command: Claude Code, Codex, Cursor, aider, a bash script,
CI. It costs one file to maintain, adds zero tokens to requests that don't use it, and
keeps a single tested code path.

MCP earns its keep in exactly two places, and they're real:

1. **Harnesses without shell access** — Claude Desktop, chat clients, some IDE
   integrations. The CLI is invisible to them.
2. **Connection reuse and session state.** For Postgres, BigQuery, Trino, and Databricks,
   connection setup dominates a short query. A persistent server also preserves temp
   tables, session settings, and open transactions across calls, which a fresh process
   per query cannot.

So: both, in that order, sharing one core.

### E1. The Harlequin skill

`SKILL.md` in this repo, published on the site, and installable. Contents:

- Discover the setup first: `hsql info --json`, `hsql spec --json`
- Use profiles, never raw credentials
- Orient with `catalog --depth 2`, then scope with a pattern
- Run with `-c`; pick a format on purpose; respect the limit; check for truncation
- Branch on exit codes
- Prefer `--read-only` unless the human asked for a write
- **When to stop and hand off to the human's TUI** — this is the part nobody else will
  write, and it's what makes the skill feel like Harlequin's rather than generic

### E2. `hsql mcp`

`hsql mcp --profile prod` starts a stdio MCP server. Deliberately small tool surface —
every tool is tokens in every request:

- `run_query(sql, format?, limit?)`
- `list_catalog(pattern?, depth?)`
- `describe_object(name)`
- `explain_query(sql)`
- `format_sql(sql)`

Plus MCP *resources* for the catalog and for the docs (`llms-full.txt`), which is the
idiomatic way to expose reference material without spending tool-schema tokens.

Read-only by default; writes require an explicit `--allow-writes`. The MCP dependencies
ship as an optional extra so the base install stays lean.

**Sequenced last, deliberately** (M6). Everything above it — the execution core, the
catalog navigation, the capability flags, the skill — is what an MCP server would be a
wrapper *around*. Building it before those settle means designing tool schemas against a
moving target and then maintaining the mistakes. Shipping it last also means we'll know
from real skill usage which five tools are actually worth their tokens, rather than
guessing.

### Stories

- **E1s.** As an agent in Claude Code, I install the Harlequin skill and immediately use
  the right flags, formats, and limits without trial and error.
- **E2s.** As a human in Claude Desktop with no shell, I add the Harlequin MCP server and
  ask questions about my Postgres database.
- **E3s.** As an agent running twenty queries against BigQuery, I reuse one connection
  instead of paying setup cost twenty times.

---

## 10. Workstream F — Human ↔ agent handoff

This is the part only Harlequin can build, and I'd resist the temptation to sequence it
last just because it's the least conventional.

- **`hsql open query.sql`** launches the TUI with the query loaded in a new buffer,
  **not executed**. The agent drafts; the human reviews, edits, and runs. Buffers already
  persist through `editor_cache.py`, so the machinery is close at hand. Having a verb for
  this on the agent-facing command is exactly the ergonomic win the two-command split
  buys us.
- **"Copy CLI command" action in the TUI** — writes `hsql -P prod -c '<current buffer>'
  --csv` to the clipboard, so a human can hand their agent something exactly
  reproducible.
- **Readable history** (Workstream B) closes the loop the other way.

### On issue #952 — natural language to SQL

I don't think Harlequin should embed an LLM. No API keys, no model selection, no
provider coupling, no support burden for someone else's rate limits.

But there's a middle path that fits Harlequin's plugin ethos and answers the actual
request: **a configurable external-command hook.** Let the user define, in their config,
a command that receives the current buffer (and optionally the catalog) on stdin and
whose stdout is inserted into the editor. Then `Ctrl+G` runs *whatever the user
already trusts* — `claude -p`, `llm`, a local model, a company-internal endpoint. Same
shape as the existing external-editor request ([#767](https://github.com/tconbeer/harlequin/issues/767),
[#769](https://github.com/tconbeer/harlequin/issues/769)), and it inherits their
credentials rather than asking for new ones.

### Stories

- **F1.** As an agent, I draft a migration and `hsql open` it in the human's TUI for
  review rather than executing it myself.
- **F2.** As a human, I explore interactively, then copy a reproducible `hsql` command for
  my agent to parameterize and run in CI.
- **F3.** As a human, I press one key in the TUI and my own AI tool rewrites my query —
  using my own credentials, not Harlequin's.

---

## 11. Roadmap

Because the CLI is a separate command, **every milestone here is purely additive to
`harlequin`** and can ship in 2.x minors. Nothing waits for a major version.

| Milestone | Theme | Contents |
| --- | --- | --- |
| **M0** | Secure the name | Publish `hsql` to PyPI as a metapackage depending on `harlequin`, so `pip install hsql` works today and the name can't be taken while the rest of this ships. |
| **M1** | `hsql` | Second console script; extract the shared execution core; import-linter rule and cold-start benchmark in CI. `-c`, `-f`, stdin, `-o`, `--format` + shorthands, default `--limit 500`, truncation notices, `--stats`, exit codes, `--on-error`, `--color`/`NO_COLOR`, plain errors. Unknown-option hint on `harlequin`. Docs: the "Headless & Agents" topic, seeded. **Closes #524.** |
| **M2** | Self-description & safety | `catalog` (one level below the match, child counts, node budget), `describe`, `info --json`, `spec --json`, `fmt`, `config validate/show/schema/init`, capability flags, secret option type + declarative redaction (**#667**), env interpolation (**#898**), `--read-only`, `--timeout`, `--dry-run`. Published JSON Schema. Stretch: `find` + `implements_catalog_search`, optional `fetch_descendants`. |
| **M3** | Docs for machines | `llms.txt`, `llms-full.txt`, raw `.md` routes, Docs API v1, copy-as-markdown, homepage positioning, `AGENTS.md`. |
| **M4** | Skill & handoff | Skill, `hsql open`, JSONL history, "Copy CLI command", external-command hook. |
| **M5** | Scale | Streaming output, `--offset`/pagination, memory work for large results (**#875**). |
| **M6** | MCP | `hsql mcp` over the M1 execution core: `run_query`, `list_catalog`, `describe_object`, `explain_query`, `format_sql`, plus catalog and docs resources. Read-only by default. |

**On M1.** Implementation is planned in detail in
[the M1 technical plan](./m1-hsql-technical-plan.md): the module architecture, the
refactors that have to land first, and an eight-PR sequence across two releases. A few
figures and defaults in this document were corrected against measurements taken while
writing it; §8 there lists them. PRs 1–6 shipped and PR 7, the docs topic, is in flight;
PR 8, the agent eval suite, was not built.

**On M2.** Likewise in [the M2 technical plan](./m2-hsql-technical-plan.md), across three
releases rather than one: **config and self-description first**, since they fix bugs that
exist today and need no adapter change at all; then the catalog, with search implemented for
both in-tree adapters rather than left as a stretch; then the safety flags, which need
additive members on the adapter contract and therefore an ecosystem rollout. The amendments
marked above are the corrections that came out of measuring it. This table's M2 row is
otherwise all subtraction: the introspection surface is mode options rather than
subcommands, and `describe`, `fmt`, `--dry-run`, `--single-transaction`, `--password-stdin`,
recursive `--depth`, the node budget, child counts and the `compact` format are all cut —
each for a reason given in §7 of that plan.

**On M0.** Name availability is the only thing in this plan that someone else can take
while we deliberate, which is why it's a milestone rather than a task. It's also cheap
enough to do this week.

A few things worth getting right the first time:

- **Metapackage, not a second copy of the artifact.** `hsql` should be a small
  distribution whose only dependency is `harlequin`. Republishing the same built wheel
  under a second name would put duplicate modules on disk and conflict outright if a user
  installed both.
- **Version it independently and float the dependency** — `hsql 0.1.0` requiring
  `harlequin>=2.x` — so it doesn't need a release every time Harlequin cuts one. (Also
  reversed once M1 shipped: it now takes Harlequin's version and pins it. See the status
  note below.)
- **No placeholder console script.** It's tempting to ship an `hsql` command that prints
  "coming soon," but from M1 the `harlequin` distribution itself provides that entry
  point, and two installed distributions claiming the same script name is a mess to
  unwind. `pip install hsql` giving you Harlequin, plus a README that says what's coming,
  is honest enough. (M1 shipped, and the metapackage does declare the script now — see
  the status note below. The half of this that held up is "no *placeholder*": it points
  at Harlequin's entry point rather than standing in for it.)
- Reserve the same name anywhere else Harlequin is published or packaged while we're at
  it.

This is a genuine, working package rather than a squat, which also keeps it on the right
side of PyPI's naming policy.

*Status: the metapackage lives in `packaging/hsql/`, and M1 changed two of the decisions
above.*

*It declares `hsql = "harlequin.hsql:main"` — the same entry point Harlequin's own script
uses, so the two console scripts are byte-identical and co-installing them is a no-op.
Without it, `uvx hsql` still ran, but uv warned that the executable came from a
dependency and suggested `uvx --from harlequin hsql`, on the one command line this
package exists to make short.*

*And it ships with Harlequin rather than on its own cadence: `publish.yml` builds and
uploads both from one release, and the metapackage carries Harlequin's version number and
pins that release exactly — `release.yml` sets all three numbers with `uv version` and
`uv add`, and `test_packaging.py` fails the release PR if they ever disagree. So `hsql
2.9.0` is `harlequin 2.9.0`, whichever name a caller installs by. The independent cadence
was the right call while this package was a reserved name and nothing else; once it
points at an entry point that only some releases have, a floating floor is a promise the
metapackage cannot keep.*

`--read-only` sits in M2 rather than M1 only because it requires an adapter-interface
addition and therefore an ecosystem rollout; if that lands early, pull it forward. It's
the flag that makes everything else socially acceptable.

### If only three things get built

1. **`hsql`** with `-c` / `-f` / `-o` / `--format`, a strict stdout/stderr split,
   documented exit codes, and honest truncation.
2. **`hsql catalog --format compact`**.
3. **`llms.txt` + raw markdown docs + the "Headless & Agents" topic + the skill.**

That trio takes an agent from "reinvents psql badly" to "writes a correct query against
any of fifteen databases on the first try."

---

## 12. Risks and open questions

- **Two front doors can drift.** The whole bet rests on one shared execution core. If
  `hsql` grows its own query logic, or the TUI keeps a parallel path, we've built two
  products. Mitigation: extract the core in M1 and refactor the TUI onto it in the same
  milestone, not later.
- **Discoverability of `hsql`.** Nobody guesses it exists; `harlequin -c` is guessable.
  Mitigated by the unknown-option hint, the docs topic named for the capability rather
  than the binary, homepage positioning, and the skill. Worth watching — if support
  questions suggest people can't find it, revisit.
- **Name mindshare.** `hsql` competes with HyperSQL in search results. No PATH or PyPI
  collision, but SEO for "hsql" will take work; docs should always spell it "hsql, the
  Harlequin CLI" on first use.
- **Adapter ecosystem lift.** `--read-only`, `validate_sql`, catalog search, and any bulk
  `fetch_descendants` are optional adapter methods. `hsql` must degrade gracefully and
  *say which adapter lacks what* — hence capability flags. Third-party adapters will lag;
  plan for a long tail.
- **Catalog fetch cost is the sharpest edge in the plan.** The default is safe by
  construction (§6), but `--depth 2` on a wide schema is an N+1 walk on most adapters,
  and it's also the single most useful catalog call an agent can make. Child counts, the
  node budget, and `--stats` are what keep that honest until adapters can serve it in
  one query.
- **Cold start.** Measured rather than feared, and worse than this section assumed: a
  bare `harlequin --version` takes 1.3s today. Lazy entry-point resolution is a
  requirement of M1, not a contingency — `load_adapter_plugins()` imports every installed
  adapter on every invocation, worth 160ms with four installed and unbounded as the
  ecosystem grows. An import-linter rule in this repo would also never have caught the
  largest single cause, which is upstream: `harlequin.adapter` pays 265ms for
  `textual-fastdatatable`, whose backend needs no Textual but whose package `__init__`
  imports the DataTable widget. See the M1 technical plan.
- **Memory.** Materializing an Arrow table for a large export is the same failure as
  #875, and it is not fixed in M1. See the streaming note in §5.
- **Secrets.** `info --json`, `config show`, error messages, and history are all leak
  surfaces, and agents paste output into transcripts. Mitigated structurally by the
  secret option type in §7 (**#667**) rather than by remembering to redact at each site —
  but note that connection strings and query history sit outside that mechanism and need
  their own handling.
- **Two help surfaces to keep coherent.** `harlequin --help` and `hsql --help` share the
  adapter option matrix and must not disagree. Generate both from the same option
  definitions.
- **Docs drift.** `spec --json` output and the site's config spec must be generated from
  the same source, or they'll disagree within two releases.
- **MCP maintenance.** Only worth it as a thin wrapper. If it starts growing its own
  query logic, that's the signal we got the layering wrong.

## 13. How we'd know it worked

- `hsql -c "select 1"` against DuckDB completes in **< 300ms**, tracked in CI.
- The `hsql` module graph contains **no `textual` import**, enforced in CI.
- **An agent eval suite in CI**: a handful of realistic tasks ("find the top 10 customers
  by revenue") run against fixture databases with a small model, scored on wrong-flag
  retries and task completion. This is the metric that actually matters, and it's the one
  that would keep the CLI honest as it grows. I'd build it in M1 with three tasks and
  expand it.
- An agent given only `hsql --help` completes a query task with **zero** wrong-flag
  retries.
- `llms.txt` is served, and every docs page is fetchable as clean markdown.
- #524, #667, and #898 closed; #952 answered without embedding an LLM.
- Qualitatively: someone writes "just use hsql, it works the same everywhere" in a thread
  about getting an agent to talk to a database.
