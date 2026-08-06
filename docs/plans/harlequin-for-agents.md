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

The pitch writes itself: **`harlequin -P prod -c "select 1"` works the same way against
every database your team uses.** One CLI contract, one set of flags, one output format
vocabulary, one exit-code scheme — and when the agent needs a human, the human opens
the same tool and sees the same catalog.

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

1. **The TUI is the product; headless is a first-class mode of it, not a second
   product.** Same adapters, same config discovery, same profiles, same `--limit`
   semantics, same error types. Every headless feature must reuse a TUI code path or
   the TUI must be refactored to reuse the headless one.

2. **stdout is data; stderr is narration.** Results go to stdout and nothing else does.
   Timings, row counts, truncation notices, warnings, and errors go to stderr. This is
   already the intent in `exception.py` — make it a hard, tested contract.

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
   make Harlequin excellent for the agent already sitting next to the user. (See §9 for
   how this answers issue #952.)

---

## 4. Workstream A — Headless execution (`-c`)

Closes [#524](https://github.com/tconbeer/harlequin/issues/524). This is the
foundation; everything else in this plan assumes it.

### Proposed surface

```
harlequin [OPTIONS] [CONN_STR]...

  -c, --command TEXT     Execute SQL and exit. Repeatable. Implies headless mode.
      --file PATH        Execute SQL from a file (or `-` for stdin). Repeatable.
  -o, --output PATH      Write results to PATH instead of stdout.
  -F, --format NAME      Output format (see below). Default: table.
      --csv/--json/--jsonl/--markdown/--vertical    Format shorthands.
      --no-header        Omit the header row.
      --null-string STR  Render NULL as STR. Default: empty for csv, "NULL" for table.
  -l, --limit N          Max rows per result set. Headless default: 500. 0 = no limit.
      --result all|last|N  Which result set(s) to emit. Default: all (text), last (data).
      --on-error stop|continue    Default: stop.
      --stats            Write a one-line JSON summary to stderr.
      --color auto|always|never   Default: never in headless mode.
      --timeout SECONDS  Cancel the query and exit non-zero.
      --read-only        Refuse to run if the adapter can't enforce read-only.
      --dry-run          Parse/validate (and EXPLAIN where supported) without executing.
      --single-transaction        Wrap the whole script; roll back on any error.
```

Precedence stays as it is today: CLI > env > `--config-path` > cwd config > user config
dir > home > defaults. Profiles work identically in headless mode, which is the whole
point — `harlequin -P prod -c ...` means the agent never handles a credential.

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

### Behaviors that matter more than the flag list

**Truncation is always announced.** If `--limit` bites, stderr gets
`note: results truncated at 500 rows (--limit)`, and text formats append a visible
`… 500 of N rows` footer. The current default of 100,000 rows is right for a TUI and a
catastrophe for an agent; headless defaults to 500.

**Row counts and timing go to stderr**, so `harlequin -c "select 1" --csv > out.csv`
produces a clean file while the agent still sees `1 row in 0.02s`.

**`--stats` emits one line of JSON to stderr** for any output format:

```json
{"status":"ok","statements":1,"rows":500,"truncated":true,"limit":500,
 "elapsed_ms":412,"columns":[{"name":"id","type":"BIGINT"}]}
```

This is how an agent gets structured metadata without polluting stdout, and it works
identically whether stdout is CSV or Parquet.

**Errors are plain in headless mode.** No Rich panels, no ANSI, no box drawing:
`harlequin: error: relation "usres" does not exist` on stderr. The panel is a TUI
affordance.

**Exit codes:**

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Query error (the database rejected the SQL) |
| 2 | Usage / config error (bad flag, bad profile, bad TOML) |
| 3 | Connection error (couldn't reach or authenticate to the database) |
| 4 | Timeout / cancelled |
| 130 | SIGINT |

**Cold start is a feature.** An agent may run twenty queries in a task. Headless mode
must lazy-import and never touch Textual. Target: **`harlequin -c "select 1"` against
DuckDB in under 300ms**, tracked as a benchmark in CI.

**Large results stream.** Headless should not materialize a full Arrow table for a
CSV/JSONL export. This also chips at [#875](https://github.com/tconbeer/harlequin/issues/875).

### User stories

- **A1.** As an agent, I run `harlequin -P prod -c "select count(*) from orders"` and get
  a small, aligned result on stdout and exit code 0, so I can report a number to the
  human without parsing a TUI.
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
- **A6.** As an agent, I run a multi-statement script with `--file setup.sql
  --single-transaction` and either all of it applies or none of it does.
- **A7.** As a human, I set `read_only = true` in my agent profile so the agent
  physically cannot write to prod, and I can point at that line in the config when
  someone asks whether it's safe.
- **A8.** As a human, I use `harlequin -c` in a Makefile or CI job because it's the same
  tool and same profile I already use interactively.

### Open decision: the `-f` collision

`-f` is currently `--show-files`. psql uses `-f` for "execute this SQL file," and that's
what agents will type. Options, in my order of preference:

1. **Reclaim `-f` for `--file` in 3.0.** In 2.x, `--file` is long-form only and `-f`
   emits a deprecation notice pointing at `--show-files`. Costs one breaking change in a
   major version; buys psql muscle memory forever.
2. Ship `--file` long-form only and never reclaim `-f`. Safe, slightly worse.
3. Make `-f` mode-dependent. **Don't** — mode-dependent flags are exactly what agents
   get wrong.

Related, lower stakes: `-t` is `--theme` here and "tuples only" in psql. Leave it; the
psql behavior is available as `--no-header`, and it should be called out in the docs
under a short "differences from psql" table.

---

## 5. Workstream B — Self-description and introspection

If Workstream A is what lets an agent *run* a query, this is what lets it write a
correct one on the first try.

### `harlequin catalog`

The highest-leverage feature in this plan after `-c`. Harlequin already normalizes the
catalog across every adapter; today that value is locked inside the TUI.

```
harlequin catalog [PATTERN] [--depth N] [--format compact|json|md|tree]
harlequin describe <OBJECT>
```

Token efficiency is the design constraint. A JSON dump of a 500-table warehouse is
useless to an agent. So:

- `--depth 1|2` returns just databases, or databases and schemas — cheap orientation.
- `PATTERN` (e.g. `analytics.*`, `*.orders`) scopes the expensive part.
- `--format compact` emits one line per table: `analytics.orders(id BIGINT, customer_id
  BIGINT, total DECIMAL(18,2), created_at TIMESTAMP)`. Roughly an order of magnitude
  cheaper than pretty JSON and perfectly readable by a model.
- `describe` returns one object in full, including comments and row counts where the
  adapter can supply them.

**Stories:**

- **B1.** As an agent, I run `harlequin -P prod catalog --depth 2` to learn what schemas
  exist before spending tokens on columns.
- **B2.** As an agent, I run `harlequin -P prod catalog 'analytics.*' --format compact`
  and get every table and column type in that schema in a few hundred tokens, without
  knowing whether the backend is Postgres or BigQuery.
- **B3.** As an agent, I write a correct join on the first attempt because I had real
  column names and types, not guesses from a table name.

### `harlequin spec --json`

Dump the full, *installed* CLI surface — including options contributed by whichever
adapter plugins are present — as JSON. Harlequin already builds these dynamically from
`AbstractOption`, so the data exists; it just isn't reachable.

This is unusually valuable here precisely because the option surface is plugin-driven.
No model's pretraining can know that this machine has `harlequin-databricks` installed
with its particular options. Self-description turns a guessing game into a lookup.

- **B4.** As an agent, I run `harlequin spec --json` once at the start of a task and
  never hallucinate a flag for the rest of it.

### `harlequin info --json`

The debug-info screen, headless: Harlequin version, installed adapters and their
versions, discovered config files in precedence order, the active profile, and
**capability flags per adapter** (`implements_cancel`, `implements_copy`,
`implements_read_only`, `implements_validate_sql`, `supports_transactions`).
Credentials and connection strings redacted.

Capability flags matter: they let an agent know *not* to try `--dry-run` on an adapter
that can't validate, instead of trying and failing.

- **B5.** As an agent debugging a human's setup, I run `harlequin info --json` and can
  tell them exactly which config file is winning and why.
- **B6.** As an agent, I check capability flags before offering the human a feature their
  adapter doesn't support.

### `harlequin fmt`

Expose sqlfmt, which is already a dependency. `harlequin fmt query.sql`,
`harlequin fmt -c "select 1"`, `--check` for CI. Agents write inconsistent SQL; this
normalizes it before it lands in a human's repo. Cheap, and a genuinely nice human
feature too.

### `harlequin history`

Query history is already cached per connection — but as a pickle. Expose it
(`harlequin history --json -n 20`) and additionally write an append-only JSONL log, so
both agents and humans can read it with ordinary tools.

- **B7.** As an agent joining a task mid-stream, I read the human's recent query history
  and continue their line of investigation instead of starting over.

---

## 6. Workstream C — The config file as a formal contract

Agents write config files. Today they'd be guessing at TOML structure from prose docs,
and a wrong guess produces a confusing runtime error.

### Deliverables

1. **A published JSON Schema** for `.harlequin.toml`, at
   `https://harlequin.sh/schemas/config/v1.json` and shipped in the package. Enables the
   `#:schema` comment for taplo / Even Better TOML — editor validation for humans, and a
   verifiable target for agents.
   - Wrinkle: adapter options are dynamic. Solution: publish a base schema for the
     site, and have `harlequin config schema --json` generate a locally-accurate one
     that includes the installed adapters' options.
2. **`harlequin config validate [--json]`** — structured diagnostics with file paths and
   line numbers, so an agent can fix its own mistake without a human in the loop. The
   existing `_raise_on_bad_schema` messages are already good; they just need a machine
   channel.
3. **`harlequin config show [--json]`** — the effective merged config, with provenance
   per key (`limit = 500  # from ~/.harlequin.toml`). This is the single best
   troubleshooting artifact we could produce.
4. **`harlequin config init --non-interactive`** — the wizard is questionary-only today,
   so agents can't use it. A non-interactive path that edits TOML through tomlkit
   (preserving the human's comments and formatting) means an agent can safely modify a
   config it didn't write.
5. **Environment variable interpolation** (`${DB_PASSWORD}`) in config values — closes
   [#898](https://github.com/tconbeer/harlequin/issues/898), and it's a prerequisite for
   agents and CI ever touching a config that involves secrets.
6. **A formal spec page on the site**: discovery order, merge semantics, precedence,
   every key with its type and default, and the reserved/invalid names.

### Stories

- **C1.** As an agent, I add a `prod` profile to a human's existing config without
  clobbering their comments or reformatting the file.
- **C2.** As an agent, I validate the config I just wrote and get a line number when I
  get it wrong.
- **C3.** As a human, my editor red-squiggles a typo'd key before I ever run Harlequin.
- **C4.** As an agent, I write a config that references `${SNOWFLAKE_PASSWORD}` and never
  see the secret itself.

---

## 7. Workstream D — Docs that machines can read (harlequin.sh)

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

### D5. A "Harlequin for Agents" docs topic

A first-class topic in the sidebar, not a footnote. Pages:

- Headless Mode — the `-c` / `--file` tutorial
- Output Formats — the table from §4, with examples
- Exit Codes and Error Handling
- Catalog and Introspection
- Safety: read-only, timeouts, dry runs
- Differences from `psql` — the short compatibility table
- Using Harlequin with Claude Code and other agents — the skill
- The MCP server
- Config File Spec (links to Workstream C)

This is positioning as much as documentation: *the SQL client your agent already knows
how to use.* It's also the page a human sends to their agent, which makes it the entry
point for P3.

### D6. `AGENTS.md` in the repos

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

## 8. Workstream E — Integrations: skill first, MCP second

### The recommendation, and why

**Ship a skill first. Make MCP a thin wrapper over the same execution core, not a
parallel implementation.**

A good CLI plus a skill — one markdown file that teaches the CLI — works in *any* agent
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

- Discover the setup first: `harlequin info --json`, `harlequin spec --json`
- Use profiles, never raw credentials
- Orient with `catalog --depth 2`, then scope with a pattern
- Run with `-c`; pick a format on purpose; respect the limit; check for truncation
- Branch on exit codes
- Prefer `--read-only` unless the human asked for a write
- **When to stop and hand off to the human's TUI** — this is the part nobody else will
  write, and it's what makes the skill feel like Harlequin's rather than generic

### E2. `harlequin mcp`

`harlequin mcp --profile prod` starts a stdio MCP server. Deliberately small tool
surface — every tool is tokens in every request:

- `run_query(sql, format?, limit?)`
- `list_catalog(pattern?, depth?)`
- `describe_object(name)`
- `explain_query(sql)`
- `format_sql(sql)`

Plus MCP *resources* for the catalog and for the docs (`llms-full.txt`), which is the
idiomatic way to expose reference material without spending tool-schema tokens.

Read-only by default; writes require an explicit `--allow-writes`. Shipped as an optional
extra (`harlequin[mcp]`) so the base install stays lean.

### Stories

- **E1s.** As an agent in Claude Code, I install the Harlequin skill and immediately use
  the right flags, formats, and limits without trial and error.
- **E2s.** As a human in Claude Desktop with no shell, I add the Harlequin MCP server and
  ask questions about my Postgres database.
- **E3s.** As an agent running twenty queries against BigQuery, I reuse one connection
  instead of paying setup cost twenty times.

---

## 9. Workstream F — Human ↔ agent handoff

This is the part only Harlequin can build, and I'd resist the temptation to sequence it
last just because it's the least conventional.

- **`harlequin --new-buffer query.sql`** (or `-c "..." --open`) opens the TUI with the
  query loaded in a new buffer, **not executed**. The agent drafts; the human reviews,
  edits, and runs. Buffers already persist through `editor_cache.py`, so the machinery
  is close at hand.
- **"Copy CLI command" action in the TUI** — writes `harlequin -P prod -c '<current
  buffer>' --csv` to the clipboard, so a human can hand their agent something exactly
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

- **F1.** As an agent, I draft a migration and open it in the human's TUI for review
  rather than executing it myself.
- **F2.** As a human, I explore interactively, then copy a reproducible CLI command for my
  agent to parameterize and run in CI.
- **F3.** As a human, I press one key in the TUI and my own AI tool rewrites my query —
  using my own credentials, not Harlequin's.

---

## 10. Roadmap

| Milestone | Theme | Contents |
| --- | --- | --- |
| **M1** | Headless | `-c`, `--file`, stdin, `-o`, `-F` + shorthands, headless `--limit` default, truncation notices, `--stats`, exit codes, `--on-error`, `--color`/`NO_COLOR`, plain errors, cold-start benchmark. Docs: Headless Mode pages, updated `running.md`. **Closes #524.** |
| **M2** | Self-description & safety | `catalog`, `describe`, `info --json`, `spec --json`, `fmt`, `config validate/show/schema/init`, capability flags, env interpolation (**#898**), `--read-only`, `--timeout`, `--dry-run`. Published JSON Schema. |
| **M3** | Docs for machines | `llms.txt`, `llms-full.txt`, raw `.md` routes, Docs API v1, copy-as-markdown, "Harlequin for Agents" topic, `AGENTS.md`. |
| **M4** | Integrations & handoff | Skill, `harlequin mcp`, `--new-buffer`, JSONL history, "Copy CLI command", external-command hook. |
| **M5** | Scale | Streaming output, `--offset`/pagination, memory work for large results (**#875**). |

`--read-only` sits in M2 rather than M1 only because it requires an adapter-interface
addition and therefore an ecosystem rollout; if that lands early, pull it forward. It's
the flag that makes everything else socially acceptable.

### If only three things get built

1. **`-c` / `--file` / `-o` / `-F`** with a strict stdout/stderr split, documented exit
   codes, and honest truncation.
2. **`harlequin catalog --format compact`**.
3. **`llms.txt` + raw markdown docs + the "Harlequin for Agents" topic + the skill.**

That trio takes an agent from "reinvents psql badly" to "writes a correct query against
any of fifteen databases on the first try."

---

## 11. Risks and open questions

- **`-f` collision.** Needs a decision (§4). Everything else in M1 is additive.
- **Adapter ecosystem lift.** `--read-only`, catalog depth control, and `validate_sql`
  are optional adapter methods. Headless mode must degrade gracefully and *say which
  adapter lacks what* — hence capability flags. Third-party adapters will lag; plan for
  a long tail.
- **Cold start.** Textual import cost is the main threat to the 300ms target. Headless
  must not import the TUI at all, which may mean restructuring `cli.py` so the command
  builder doesn't pull in `harlequin.app`.
- **Memory.** Materializing an Arrow table for a large export is the same failure as
  #875. Streaming needs to be designed in from M1's format layer, not retrofitted.
- **Secrets.** `info --json`, `config show`, error messages, and history must all redact
  connection strings and passwords. One leak here is a security incident, and agents
  paste output into transcripts.
- **Scope creep.** Harlequin is a TUI. The guardrail: every headless feature reuses a TUI
  code path, and headless mode ships no capability the TUI doesn't already have.
- **Docs drift.** `spec --json` output and the site's config spec must be generated from
  the same source, or they'll disagree within two releases. Generate the docs tables from
  the option definitions.
- **MCP maintenance.** Only worth it as a thin wrapper. If it starts growing its own
  query logic, that's the signal we got the layering wrong.

## 12. How we'd know it worked

- `harlequin -c "select 1"` against DuckDB completes in **< 300ms**, tracked in CI.
- **An agent eval suite in CI**: a handful of realistic tasks ("find the top 10 customers
  by revenue") run against fixture databases with a small model, scored on
  wrong-flag retries and task completion. This is the metric that actually matters, and
  it's the one that would keep the CLI honest as it grows. I'd build it in M1 with three
  tasks and expand it.
- An agent given only `harlequin --help` completes a query task with **zero** wrong-flag
  retries.
- `llms.txt` is served, and every docs page is fetchable as clean markdown.
- #524 and #898 closed; #952 answered without embedding an LLM.
- Qualitatively: someone writes "just use harlequin, it works the same everywhere" in a
  thread about getting an agent to talk to a database.
