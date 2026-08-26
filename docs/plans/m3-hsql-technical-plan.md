# M3 Technical Plan — docs a machine can read, and the skill

Implementation plan for milestone M3 of [Harlequin for agents](./harlequin-for-agents.md),
following [M1](./m1-hsql-technical-plan.md) and [M2](./m2-hsql-technical-plan.md). M1 built
the command, M2 taught it to describe itself. This one is about everything an agent reads
*before* it runs `hsql`: the documentation, and the skill that tells it which flag to reach
for.

**M3's scope, from the roadmap:** `llms.txt`, `llms-full.txt`, raw `.md` routes, Docs API v1,
copy-as-markdown, homepage positioning, and `AGENTS.md` — Workstream D. **The Harlequin skill
(E1) is pulled forward from M4** (Ted's call). The rest of M4 — `hsql open`, JSONL history,
"Copy CLI command", the external-command hook — stays in M4.

**Where M2 actually got to.** PRs 1–12, 15 and 16 shipped: the per-file/per-profile config
work, the five self-description modes, secrets and `${VAR}`, `--catalog`, `type_name` and
`--catalog-search`. PRs 13 (`--read-only` and the capability declarations), 14 (`--timeout`)
and 17 (the safety docs) are in flight and unshipped, which is a real dependency here and not
a footnote: two of the nine things the product plan says the skill should teach are flags that
do not exist yet (§1.9).

**Two repos, and most of the work is in the other one.** `tconbeer/harlequin-web` is where
`llms.txt`, the raw routes, the API and the docs topic live. That repo has no tests, no CI
beyond a Vercel preview build, and a `pnpm lint` pre-commit hook. M3 is the first milestone
that has to treat it as a build target rather than a place to paste prose.

**Bottom line up front.** Three claims, each measured below:

1. **Docs written for machines have to be true, and today they are not.** `hsql --parquet`
   and `hsql --results` are documented on the site, in the packaged README, *and* in the
   homepage's terminal mock-up; both exit 2 with "No such option" (§1.4). The one page
   written for agents prints `&lbrace"status":"ok"…` where `{"status":"ok"…}` belongs (§1.3).
   `https://harlequin.sh/schemas/config/v1.json`, which every schema `hsql --config schema`
   generates names as its `$id`, is a 404 (§1.5). Nothing in either repo checks any of this.
   **The first thing M3 builds is the check, not the corpus** — publishing more machine-read
   text over an unchecked pipeline multiplies the blast radius of exactly this bug.
2. **A page's markdown source is not its markdown output, and four features want the same
   transform.** 24 of 55 pages open a `<script>` block; there are 168 `<Key>` tags, 11
   `<Figure src={identifier}>` whose `src` is a JS binding rather than a path, and 56
   relative links that mean nothing outside the site's router. The raw route, `llms-full.txt`,
   the Docs API and the copy button are four consumers of **one** sanitizer, and a raw route
   that emits `<Figure src={init}>` is worse than no raw route (§3.2).
3. **The skill's value is knowing what not to say.** It ships *in the wheel* and prints from
   `hsql --skill`, so the text an agent installs is the version of `hsql` that is installed;
   and it teaches `--info`/`--spec`/`--help -a` rather than restating them, so it stays
   ~1,000 tokens and cannot go stale about adapters it has never heard of (§3.5).

---

## 1. Where things are today

Measured on this checkout — 2.10.0 plus the unreleased 2.11 catalog work — and on
`tconbeer/harlequin-web` at `31b2051`, built with pnpm 10.33 / Node 22.22 and served from
`pnpm preview`.

### 1.1 The site is 55 markdown files, a glob, and no tests

`src/docs/**/*.md` is the source of truth for both page content and the sidebar: there is no
nav config. `src/routes/api/docs/+server.ts` globs every `.md`, reads `title` and `menuOrder`
out of the frontmatter, and sorts topics and pages together. Rendering is mdsvex through
`src/routes/docs/[topic]/[[page]]/`, with ISR at one hour and a hardcoded `repoMap` for the
third-party adapter star counts.

```
55 docs pages          122,992 bytes of markdown
 3 blog posts
 0 tests                (the repo has no test runner; `pnpm lint` is prettier + eslint)
```

There is one deployment gate — a Vercel preview build — and it fails only on a build error. A
page that renders a component that does not exist fails it; a page that documents a flag that
does not exist does not.

### 1.2 What one docs page costs an agent today

Production build, served locally, bytes on the wire for the HTML document alone:

```
/docs/getting-started/usage    66,187 bytes HTML   vs  7,369 bytes markdown source   9.0x
/docs/getting-started/hsql     72,523 bytes HTML   vs  5,761 bytes markdown source  12.6x
```

The product plan's D2s story claims "a tenth of the tokens," and it is right — most of a page
is the sidebar, which is rendered into every document. The whole docs corpus, with frontmatter
and script blocks stripped, is **116,101 bytes — roughly 29,000 tokens**. That number decides
the shape of D1 (§3.3): `llms-full.txt` is a real artifact but it is not a thing an agent
loads speculatively, so `llms.txt` plus per-page markdown has to be the primary path and
`llms-full.txt` the fallback, not the other way round.

### 1.3 A page's source is not markdown

What is actually in `src/docs`, counted:

| construct | count | why a raw route can't pass it through |
| --- | --- | --- |
| `<script>` block | 24 pages | imports; renders as literal text outside Svelte |
| `<Key>ctrl+q</Key>` | 168 | not markdown; means `` `ctrl+q` `` |
| `<Tip>`, `<Note>`, `<Warning>` | 22 | callouts; mean a blockquote |
| `<Figure src={init} alt=… caption=…>` | 11 | `src` is a **JS binding** from the script block, not a path |
| `<Link href="auth">` | 9 (5 relative) | means a markdown link |
| `<ThemeGallery grow=false>` | 1 | has no markdown equivalent at all |
| relative `[text](../config-file/index)` | 51 | resolves against the site router, not against a `.md` file |
| `&lbrace;` / `&rbrace;` escapes | 4 | mdsvex reserves `{`; the source can't spell one |
| ` ```output ` fences | 3 | a site-private fence language |

The brace escape is not hypothetical. `src/docs/getting-started/hsql.md:119` writes
`&lbrace"status":"ok"…` — without the closing semicolon — and the built page serves
`&amp;lbrace"status":"ok"…`. **The published page teaching agents to parse `--stats` shows
them a JSON object with `&lbrace` where `{` belongs.** It has presumably been wrong since the
page was written, because nothing renders that page in a test and no human reads a `--stats`
line closely.

### 1.4 The docs disagree with the CLI, and with themselves

Extracting every `--flag` mentioned in the hsql docs and diffing against `hsql --spec` (31
core options, plus each adapter's) finds two that do not exist:

```
$ hsql -c "select 1" --parquet -o out.pq
Error: No such option '--parquet'. Did you mean '--path'?          exit 2

$ hsql -c "select 1" --results all
Error: No such option '--results'. (Did you mean one of: '--result', '--stats'?)   exit 2
```

`--results` appears in `src/docs/getting-started/hsql.md`, in `packaging/hsql/README.md`, and
in the homepage's `hsql_features.svelte` terminal mock-up. `--parquet` appears in the first
two. The format shorthands that exist are `--csv`, `--json`, `--jsonl`, `--markdown` and
`--vertical`; parquet has never had one, and the correct spelling has always been
`--format parquet`. Every one of these is a copy-paste-and-fail for an agent, and the
`--result` case is the worse one: it is one character from the truth and the error names the
right flag, so a careless reader learns the CLI is unreliable rather than that the docs are.

This diff took nine lines of Python. Nothing runs it.

The same is true of the links between pages. Resolving all 56 relative links against the file
tree finds two that 404 on the built site:

```
/docs/hsql                                             404
  from getting-started/running.md:64 — [Using hsql](../hsql)
/docs/duckdb/troubleshooting/duckdb-version-mismatch   404
  from duckdb/index.md:11 — [Troubleshooting](troubleshooting/…), missing a ../
```

The first is the link from "Running Harlequin" to the hsql page. Two more —
`[MySQL/MariaDB](mysql/index)` and `[ODBC](odbc/index)` in `adapters.md` — resolve only
because the router 308s `/docs/x/index` to `/docs/x` (§1.1); they are correct on the site and
dead in any corpus that is files rather than routes, which is exactly what M3 publishes.

### 1.5 The site is already a publishing target, and nothing publishes to it

`src/harlequin/config_schema.py:51` sets

```python
SCHEMA_ID = "https://harlequin.sh/schemas/config/v1.json"
```

and `scripts/write_config_schema.py` writes the adapter-free document to
`src/harlequin/schemas/config-v1.json`, which ships in the wheel and is pinned by
`tests/unit_tests/test_config_schema.py`. The URL in the `$id` of every schema this repo
generates has **no route and no static file** on the site: `harlequin-web` has nothing under
`schemas/`.

So the mechanism M3 needs already has a customer that shipped in 2.10 without it: an artifact
generated in this repo, versioned here, that has to appear at a stable URL over there. There
is no workflow, no directory, and no convention for that — §3.4 is where M3 picks one.

### 1.6 The docs API is an accident, not a contract

`/api/docs` returns 5,049 bytes of `{title, menuOrder, slug}` for pages and
`{topic, slug, slugPrefix, menuOrder}` for topics. It exists to build the sidebar, and it
shows: the top-level topic's `slugPrefix` is the string `"/src"`, because the code takes the
second-to-last path segment of `/src/docs/getting-started/index.md`. There is no per-page
route, no content in the payload, no CORS header, no search, and no version in the path. It is
fine as a private endpoint and unusable as the public one D3 describes.

### 1.7 There is no skill, and no place the docs put one

`packaging/hsql/README.md` (365 lines) is the closest thing that exists — it covers install,
adapters, profiles, secrets, layouts, the catalog modes, scripting, and a "Describing hsql to
an Agent" section. It is a README: it optimizes for a human landing on PyPI, it is far too
long to load into every request, and it is where two of the wrong flags live (§1.4).

On the site, hsql is a single page — `getting-started/hsql.md`, 170 lines — inside the
Getting Started topic. D5's "Headless & Agents" topic does not exist. There is no page for
exit codes, no page for the catalog modes (they are documented only in the packaged README),
no page for safety, and no page for the config file spec beyond the three human-facing pages
under `config-file/`.

### 1.8 D6 and D7 are mostly done already

The roadmap's M3 row lists homepage positioning and `AGENTS.md`; both largely shipped during
M1/M2:

- The homepage has "One Engine, Two Interfaces," an `#hsql` section headed "Your agent's
  favorite SQL client," and three terminal mock-ups (`hsql_features.svelte`). What is left is
  a correction — it is one of the three places that says `--results`.
- `AGENTS.md` exists in `tconbeer/harlequin` and in `tconbeer/harlequin-web`. It does **not**
  exist in `tconbeer/harlequin-adapter-template`, which is the half D7 actually asked for
  ("so new adapters inherit it").

### 1.9 What the skill cannot say yet

`hsql --info` today reports two capabilities per adapter:

```json
"capabilities": { "implements_cancel": true, "implements_catalog_search": true }
```

`IMPLEMENTS_READ_ONLY` and `IMPLEMENTS_VALIDATE_SQL` arrive with M2's PR 13, and `--timeout`
with PR 14. The product plan's skill outline says "Prefer `--read-only` unless the human asked
for a write," which is advice about a flag that does not exist on `main`. §4 sequences around
this: the skill's *text* can be written now, but the PR that ships it lands after 2.12.

---

## 2. The obstacles, stated plainly

**One.** *The artifacts are generated in one repo and consumed in another, and there is no
pipe between them.* The JSON Schema needs it today; the CLI reference and the skill will need
it the moment they exist. Site builds are hermetic (a git checkout plus `pnpm i`), and the
site's build machine has no Python, so it cannot run `hsql --spec` itself.

**Two.** *Sanitization is the whole job of D2, and a half-done one is worse than none.* An
agent that fetches `.md` and gets `<Figure src={init}>` has been handed a broken tool and a
reason to distrust the next one. The transform has eleven distinct cases (§1.3) and one of
them — resolving a `src={identifier}` back to an asset URL — requires reading the script
block that the same transform is deleting.

**Three.** *Neither repo tests documentation.* This repo tests the CLI thoroughly and never
reads its own README; the site repo tests nothing. Every claim in §1.3–§1.5 is a bug that a
five-line test would have caught on the day it was written.

**Four.** *The skill has to be short and current at the same time.* Short means it cannot
enumerate fifteen adapters, thirteen formats and thirty-one options. Current means it cannot
be a snapshot of a CLI that ships every few weeks. Those pull in opposite directions unless
the skill's job is redefined as *teaching the agent which question to ask the CLI* — which is
what §3.5 does, and what keeps it at ~1,000 tokens.

**Five.** *`llms-full.txt` is 29,000 tokens.* Every design that treats it as the front door is
proposing that an agent spend a fifth of a small context window before it knows whether
Harlequin is relevant.

---

## 3. Target architecture

### 3.1 Truth first: generate what can be generated, test the rest

The principle M2 established for `config-v1.json` — *generate rather than declare, commit the
artifact, and let a test fail when the generator and the artifact disagree* — is the whole of
M3's answer to §1.4 and §1.5. Three mechanisms, in increasing order of coverage:

**(a) A flag guard, in this repo.** A unit test extracts every `--long-flag` from
`packaging/hsql/README.md` and from the skill, and asserts each one appears in `hsql --spec`
(core options, or any installed adapter's). It is nine lines, it fails on `main` today, and it
is the cheapest thing in this milestone. Positional and single-dash spellings are out of scope
— `-tAc` is a bundle, and `--with` in an install line belongs to `uv`, so the extractor takes
long flags only and the test carries a small allowlist for the ones that belong to other
programs.

**(b) A generated CLI reference.** `scripts/write_cli_reference.py` renders `hsql --spec`
(with no adapters loaded, exactly as `write_config_schema.py` does) into
`docs/generated/hsql-reference.md`: one table of options with type, default, choices, env var
and help, plus the arguments, the format list and the exit codes. Committed, pinned by a test
that regenerates and compares. This is the page the site publishes as the CLI reference, and
it *cannot* drift, because nothing hand-writes it.

**(c) A docs lint, in the site repo.** Over the sanitized corpus (§3.2), not over the sources:
every internal link resolves to a page that exists, no page contains a Svelte component tag or
an unresolved `{identifier}`, no page contains `&lbrace`, and `llms.txt` lists exactly as many
pages as `src/docs` contains. The site repo gets its first test runner for this.

What (a) and (c) cannot cover is a flag spelled correctly in prose that says the wrong thing
about it. That is a review problem, and M3 does not pretend to solve it.

### 3.2 One corpus, one sanitizer, five consumers

`src/lib/server/docs.ts` in `harlequin-web`. It is the only place that turns a `.md` source
into markdown, and everything that serves markdown calls it:

```
                        ┌─ /docs/**/*.md            (D2, raw per page)
                        ├─ /llms-full.txt           (D1, the whole corpus)
buildCorpus() ──────────┼─ /llms.txt                (D1, titles + one-liners + links)
  (glob ?raw            ├─ /api/docs/v1/…           (D3, the same markdown, as JSON)
   → sanitize)          └─ "Copy as Markdown"       (D4, fetched from the raw route)
```

`buildCorpus()` globs `/src/docs/**/*.md` with `{ query: '?raw', eager: true }` and, for each
page, returns `{ slug, topic, title, menuOrder, markdown, description }`. `sanitize()` is one
function over one page's source, applied in this order:

1. **Split the frontmatter.** `title` and `menuOrder` come out as fields; the block is dropped
   from the body, and `# {title}` is prepended, because a raw page with no heading reads as a
   fragment.
2. **Read, then delete, the `<script>` block.** Its `import X from "path"` pairs become a
   local map before the block is removed. That map is what resolves `src={init}` in step 4.
3. **Downgrade the components.** `<Key>x</Key>` → `` `x` ``; `<Tip>` → `> **Tip:** …`;
   `<Note title_text="X">` → `> **X:** …`; `<Warning>` → `> **Warning:** …`;
   `<Link href="h">t</Link>` → `[t](h)`. Nested markdown inside a callout keeps working
   because a blockquote is line-prefixed, not wrapped.
4. **Resolve `<Figure>`.** `src={init}` → the import map's path → the URL from a second
   `import.meta.glob('/src/lib/assets/**', { query: '?url', eager: true })`, absolutized
   against `https://harlequin.sh`. Emits `![alt](url)` and, if there is a caption, an
   italic line under it.
5. **Un-escape the braces.** `&lbrace;`/`&rbrace;` → `{`/`}`, and the malformed
   `&lbrace`/`&rbrace` spellings too, which is what fixes §1.3's rendered bug for every
   machine consumer. (The *rendered page* keeps its bug until the source is corrected; that
   correction is its own one-line PR, not something the sanitizer should paper over.)
6. **Absolutize links.** `[t](../config-file/index)` and `[t](auth)` resolve against the
   page's own topic and become `https://harlequin.sh/docs/…`, with a trailing `/index`
   dropped the way the router's 308 drops it. A raw `.md` file has no router under it, so a
   relative link in it is a dead link, and one the router currently rescues is a dead link
   waiting.
7. **Normalize fences.** ` ```output ` becomes an unlabelled fence; ` ```bash ` stays, and the
   `$ ` prompt is *not* added — it is injected by the site's highlighter, not present in the
   source, which means the raw output is already copy-pasteable.
8. **Refuse the unknown.** A component tag the table does not cover throws at build time
   rather than shipping. `<ThemeGallery>` gets one explicit case — dropped, replaced by a link
   to the rendered page — and the next new component gets a failing build until someone
   decides what it means in markdown.

`description`, for `llms.txt`, is the page's first sentence of prose after the heading,
truncated at ~120 characters. A `description` frontmatter key overrides it, and new pages
should set one; the derived fallback exists so that adding a page never silently degrades the
index.

### 3.3 The routes

| route | content type | how it's built |
| --- | --- | --- |
| `/llms.txt` | `text/plain` | prerendered; title, description and absolute URL per page, grouped by topic |
| `/llms-full.txt` | `text/plain` | prerendered; every page's sanitized markdown, `---`-separated, each under its canonical URL |
| `/docs/{topic}/{page}.md` | `text/markdown; charset=utf-8` | prerendered per page from the corpus |
| `/api/docs/v1` | `application/json` | the index, with `title`, `topic`, `slug`, `url`, `description` |
| `/api/docs/v1/{topic}/{page}` | `application/json` | `{ title, topic, slug, url, markdown }` |
| `/api/docs/v1/search?q=` | `application/json` | case-insensitive term match over the corpus, ranked title-then-body |
| `/schemas/config/v1.json` | `application/schema+json` | the artifact from this repo (§3.4) |

Prerendered, not ISR: the corpus is 55 files that change only when the repo does, and a
prerendered route is a static file on Vercel's CDN with no cold start and no way to fail at
request time. `/api/docs` stays exactly as it is — the sidebar depends on its shape, and v1 is
additive rather than a migration. Every `/api/docs/v1/*` response carries
`Access-Control-Allow-Origin: *`, because a browser-based agent is a real consumer and there
is nothing here that is not already public. `robots.txt` gains an explicit `Allow` for the two
`llms` files; it allows everything today, but naming them is how the convention is read.

**On the `.md` extension.** `/docs/x/y.md` rather than content negotiation on `/docs/x/y`,
because an agent constructs a URL by string-appending far more reliably than it sets an
`Accept` header, and because a static path is cacheable without `Vary`. The rendered page
links to its own `.md` twin (D4), so the discovery path is: land on HTML → see "View as
Markdown" → learn the convention → construct the rest.

### 3.4 Getting an artifact from this repo to that one

Three artifacts need a stable URL on the site: `config-v1.json` (already generated, already
committed, already referenced by a live 404), `hsql-reference.md` (§3.1b), and `SKILL.md`
(§3.5). The options:

- **The site fetches at build time** from `raw.githubusercontent.com` at a tag. One source of
  truth, no sync PR — but the site build stops being hermetic, a network blip fails a deploy,
  and a docs typo fix requires knowing which tag the site is pinned to.
- **The site vendors the artifacts, and the release workflow opens the PR.** `release.yml`
  gains a step that copies the three files into `harlequin-web/static/artifacts/` and opens a
  PR. Site builds stay hermetic and reproducible, the diff is reviewable, and the failure mode
  is a stale-but-working page rather than a failed deploy.

**Recommended: the second.** It is also the one that matches what the repo already does with
`config-v1.json` — generate, commit, pin with a test — applied one repo further along. The
site's routes serve the vendored files verbatim; the CLI reference page renders the vendored
markdown through the same layout as a hand-written page; and a `manifest.json` beside them
records which `harlequin` version they came from, which the reference page prints in a
"generated from hsql X.Y.Z" line so a reader can tell when it is behind.

This is the one piece of M3 that is more mechanism than content, and it is the piece §1.5 says
is already overdue.

### 3.5 `hsql --skill`, and the skill

**Where the text lives.** `src/harlequin/hsql/skill/SKILL.md`, shipped as package data
alongside `src/harlequin/schemas/config-v1.json` and read the way
`components/help_screen.py` reads its markdown — `Path(__file__).parent / …`. There is one
copy: the site publishes the vendored artifact (§3.4), and the flag guard (§3.1a) reads the
same file the wheel ships.

**How it's delivered.** `hsql --skill` — a mode option, like every other mode M2 added, in
`hsql/modes/skill.py`. It writes the file to stdout, and honors `-o PATH` like every other
mode, so installing it is:

```bash
hsql --skill -o ~/.claude/skills/hsql/SKILL.md      # or .claude/skills/ in a project
```

Printing a packaged file imports nothing — no adapter, no click machinery beyond the command
itself — and the import-hygiene guard says so.

**Why the wheel rather than a download.** The skill an agent installs is then, necessarily,
the skill for the `hsql` on that machine — the one place where "which version am I reading
about" cannot be got wrong. It also means the skill works with no network, which is exactly
the environment (CI, a locked-down container) where an agent is most likely to be driving a
CLI in the first place.

**What it says**, in nine short sections, budget ≤4KB / ~1,000 tokens:

1. **Ask before you assume.** `hsql --info` for versions, config files, the active profile and
   what each adapter supports; `hsql --help -a NAME` for one adapter's connection options;
   `hsql --spec` when a machine needs the option list rather than a human.
2. **Never put a credential on the command line.** Use a profile and `-P`; use `${VAR}` in the
   config file; the shell history and the process table are both readable.
3. **Orient in the catalog before writing SQL.** `hsql --catalog`, `--path db.schema`,
   `--path db.schema.table` for columns, `--catalog-search TERM` when you don't know where
   something lives — and check `--info` first, because searching is a capability not every
   adapter has.
4. **Run it.** `-c` for a statement, `-f` for a file (`-f -` for stdin), `--result` to pick
   which result set prints, `--on-error` to pick what happens after a failure.
5. **Pick a format on purpose.** `-tAc` for one value into a shell variable; `--csv` for a
   pipe; `--markdown` when the output is going into your own reply; `--format parquet -o` for
   anything large. `--format` takes the name; only csv, json, jsonl, markdown and vertical
   have shorthands.
6. **The row limit is real.** 500 by default. `--stats` puts `{"truncated": …}` on stderr —
   read it, and never `2>/dev/null`, because that is where truncation warnings and errors both
   go.
7. **Branch on the exit code.** 0 ok, 1 query error, 2 usage/config error, 3 connection error,
   4 timeout, 130 interrupted. A usage error is your bug; a query error is the SQL's.
8. **Ask before you write.** Prefer `--read-only` where the adapter declares it (`--info`
   again); say what a DDL or DML statement will do before running it.
9. **Know when to hand off.** Schema you can't disambiguate, a query the human will want to
   iterate on, anything destructive, anything that wants a human eye on 10,000 rows: stop and
   tell them to open `harlequin -P <profile>` — same profile, same adapter, same engine.

**What it deliberately does not contain:** the adapter list, any adapter's options, the full
format list, the option table, or anything about the config file's schema. All five are
one command away, and all five are what makes a skill rot.

Section 9 is the one nobody else would write, and it is also the one M4 improves: with
`hsql open query.sql`, "tell them to open Harlequin" becomes "hand them the query, loaded and
unexecuted." The skill gets that paragraph in M4, and the plan should not pretend the M3
version is the finished one.

### 3.6 The "Headless & Agents" topic

D5, in `src/docs/headless/`, with `index.md` carrying `topic: "Headless & Agents"`:

| page | source |
| --- | --- |
| `index.md` — the `hsql` overview and tutorial | moved from `getting-started/hsql.md` |
| `formats.md` — layouts, file formats, `-o`, the `-tAc` idiom | split out of the same page |
| `exit-codes.md` — the six codes, `--stats`, `--on-error`, stderr | new; today only the README says this |
| `catalog.md` — `--catalog`, `--path`, `--catalog-search` | new on the site; the packaged README has it |
| `config.md` — the config file spec, `${VAR}`, secrets, `--config` modes | new; links to the `config-file/` topic |
| `safety.md` — `--read-only`, `--timeout`, capability flags | M2's PR 17 content, if it has not landed |
| `psql.md` — the differences table | M2's PR 17 content |
| `reference.md` — the generated CLI reference | §3.1b, vendored |
| `skill.md` — what the skill is, how to install it | §3.5 |
| `mcp.md` | **not in M3** — M6 |

`getting-started/hsql.md` becomes a stub that links to the new topic, and `vercel.json` gains
a redirect. The existing URL is in the wild — it is linked from the packaged README and from
the homepage — so it does not get to 404, and a 308 to the new page is cheaper than
maintaining two copies.

### 3.7 Copy and view as markdown

In the docs layout, beside the page title: a "Copy as Markdown" button and a "View as
Markdown" link to the page's own `.md` URL. The copy variant prepends the canonical URL and
title as a two-line header, so a pasted page carries its provenance. The button fetches the
`.md` route rather than re-deriving anything — one sanitizer, five consumers, and this is
consumer five.

---

## 4. Sequencing

Two repos. The `harlequin` PRs go first, because the site's new pages vendor their artifacts,
and because the skill cannot honestly say "prefer `--read-only`" until M2's PR 13 has shipped.
Numbering continues within this milestone; `harlequin-web` has no version numbers, so its PRs
are listed in dependency order and deploy continuously.

### Release A — truth and the skill (`harlequin`, 2.13)

**PR 1 — The flag guard, and the flags it catches.** The extractor and the test (§3.1a), plus
the fixes it forces: `--results` → `--result` and `--parquet` → `--format parquet` in
`packaging/hsql/README.md`. Fails on `main` today. Bug Fixes entry — this is a documented
command line that does not run.

**PR 2 — The generated CLI reference.** `scripts/write_cli_reference.py`,
`docs/generated/hsql-reference.md`, and the regenerate-and-compare test, modeled on
`test_config_schema.py`. No user-facing behavior; the artifact is what PR 5 publishes.

**PR 3 — `hsql --skill`.** The skill text as package data, `hsql/modes/skill.py`, `-o`
support, the import-hygiene assertion that it loads no adapter, and the flag guard extended to
cover it. **Lands after M2's PR 13**, so §3.5's section 8 is true when it ships. Features
entry.

**PR 4 — The publishing workflow.** The `release.yml` step that copies `config-v1.json`,
`hsql-reference.md` and `SKILL.md` plus a `manifest.json` into a PR against `harlequin-web`
(§3.4). No changelog entry; it is release plumbing.

### Release B — the site (`harlequin-web`, continuous)

**PR 5 — `/schemas/config/v1.json`, and the artifacts directory.** The vendored files and the
routes that serve them. First, and separately from everything else, because it fixes a URL
that has been live and broken in every generated schema since 2.10 (§1.5).

**PR 6 — The corpus and the sanitizer.** `src/lib/server/docs.ts`, and the repo's first test
runner (vitest): golden files for four representative pages — one with a script block and
figures, one dense with `<Key>`, one with a callout and relative links, one plain — plus the
docs lint (§3.1c). The lint fails until the two 404 links in §1.4 are fixed, so they are
fixed here. Otherwise nothing user-visible ships in this PR; it is the foundation the next
four stand on.

**PR 7 — Raw markdown per page.** `/docs/{topic}/{page}.md`, prerendered, `text/markdown`.

**PR 8 — `llms.txt` and `llms-full.txt`.** Plus the `robots.txt` lines and, for pages that
want one, the `description` frontmatter key.

**PR 9 — Docs API v1.** Index, page and search, CORS, `/api/docs` untouched.

**PR 10 — Copy and view as markdown.** The layout buttons.

**PR 11 — The Headless & Agents topic.** The moves and splits in §3.6, the redirect for
`getting-started/hsql.md`, the vendored reference page, and the skill page. The largest
content PR in the milestone, and the one that most wants a careful read rather than a fast
one.

**PR 12 — The homepage correction.** `--results` in `hsql_features.svelte`, and a link from
the `#hsql` section to the new topic.

### Release C — the ecosystem

**PR 13 — `AGENTS.md` in `harlequin-adapter-template`.** D7's remaining half: repo layout, the
adapter contract's required members, how to run tests, and — new since the product plan was
written — `type_name`, `search_catalog()` and the capability flags M2 added, so an adapter
written from the template declares them rather than discovering them later.

**Ordering rationale.** Same as M1 and M2: the guard lands before the thing it guards, the
artifact lands before the page that publishes it, and nothing that needs another repo's
release blocks anything that doesn't. The one hard dependency across milestones is PR 3 on
M2's PR 13.

---

## 5. Testing

The M1/M2 guards stay. What M3 adds is the first coverage either repo has had of the text
humans and agents *read*, which until now has been the only untested output Harlequin
produces.

- **Every long flag in the packaged README and in the skill exists in `hsql --spec`.** With a
  small allowlist for other programs' flags (`uv`'s `--with`, `pytest`'s `--snapshot-update`),
  asserted by name so an addition to the allowlist is a visible decision.
- **`hsql --skill` is byte-identical to the packaged file**, and writing it with `-o` produces
  the same bytes as stdout — the M1 property ("the bytes are the contract"), applied to the
  one output that is not a result set.
- **`hsql --skill` imports no adapter, no tomlkit, no questionary.** A subprocess reading
  `sys.modules`, like every other import-hygiene test, with a matching `ignore_imports` entry.
- **The generated reference regenerates identically.** `test_cli_reference.py` runs the
  generator and compares to the committed file, exactly as `test_config_schema.py` does.
- **Sanitizer goldens.** Four pages, checked in as expected markdown. A component tag the
  sanitizer does not know throws, and there is a test that asserts it throws — the degradation
  path is the one nobody exercises.
- **No sanitized page contains** a `<Capitalized` tag, an unresolved `{identifier}`, `&lbrace`,
  or a relative link. Asserted over the whole corpus, so it covers pages written after this
  milestone.
- **Every internal link in the sanitized corpus resolves** to a page the corpus contains —
  after the `x/index` normalization, so a link the router rescues today is still checked. It
  fails on the two 404s in §1.4, and it is what stops the §3.6 moves from leaving holes.
- **`llms.txt` lists exactly as many pages as `src/docs` holds**, so a new page cannot be
  silently missing from the index an agent reads first.
- **The `.md` route and `llms-full.txt` agree byte for byte** for the same page. They are two
  views of one corpus, and the moment they disagree, one of them is a second sanitizer.
- **The vendored artifacts parse.** `config-v1.json` is valid JSON Schema; `manifest.json`
  names a version that matches the reference page's generated-from line.

Not tested, still: whether an agent that reads the skill picks the right flag first try. That
is the agent eval suite dropped from M1 (§13 of the product plan), and M3 is the milestone
where it would finally have a stable surface to run against — the skill is a fixed text, and
`--spec` makes the expected-flag assertions cheap to write. It stays out of scope here, and it
is the single most valuable thing that could be added to it.

---

## 6. Decisions

**Settled.**

- **The skill ships in the wheel and prints from a mode option.** `hsql --skill`, consistent
  with M2 §3.1 — modes are options, not subcommands. One copy of the text, version-matched to
  the installed CLI, and no network needed to install it.
- **The skill teaches the questions, not the answers.** No adapter list, no option table, no
  format list; `--info`, `--spec` and `--help -a` instead. This is what keeps it ~1,000 tokens
  and what keeps it from rotting.
- **One sanitizer, one corpus.** The raw route, `llms-full.txt`, the API and the copy button
  all call `buildCorpus()`. A second markdown-producing path is the same mistake as a second
  normalizer in `query.fetch()`.
- **`llms.txt` is the front door; `llms-full.txt` is the fallback.** 29,000 tokens is not an
  opening move.
- **`.md` suffix routes, not `Accept` negotiation** (§3.3).
- **`/api/docs` is untouched and v1 is additive.** The sidebar depends on the old shape,
  including its `"/src"` quirk; a fix there is a rendering change, not a docs-for-machines
  change, and it does not belong in this milestone.
- **Artifacts are vendored into the site by a release workflow, not fetched at build time**
  (§3.4) — recommended, and the one worth Ted's explicit yes before PR 4.
- **Prerender, don't ISR, for everything M3 adds.** The corpus changes when the repo does.

**Open.**

- **Does Docs API v1 ship search at all?** The corpus is 116KB; a substring-and-title ranker
  is fifty lines and no index. But `llms.txt` plus a fetch is what an agent will actually do,
  and a mediocre search endpoint is a support surface. Cut it from v1 unless there is a
  consumer asking.
- **Is there a one-command install for the skill beyond `hsql --skill -o`?** A Claude Code
  plugin marketplace repo would make it `/plugin install`, and it is an ecosystem commitment
  (a repo, a manifest, a release cadence) for an audience we cannot size yet. Recommend
  deferring until the skill has been in the wild for a release.
- **`updated_at` in the API payload.** The product plan asks for it; nothing in the repo has
  it. Frontmatter would need a hand-maintained date (which rots) or the build would need git
  mtimes (which a shallow Vercel checkout does not have). Recommend dropping the field rather
  than shipping one that lies.
- **Does `getting-started/hsql.md` move or stay?** §3.6 moves it with a 308. The alternative —
  leaving the tutorial in Getting Started and making Headless & Agents reference-only — keeps
  a live URL canonical but splits the hsql story across two topics.
- **Does the skill get a `description` tuned for tool-selection?** A skill's frontmatter
  description is what decides whether an agent loads it at all. Worth one round of deliberate
  wording, and worth measuring, which is the eval suite again.

---

## 7. Explicitly not in M3

- **`hsql mcp`** — M6, and the skill is deliberately the thing that ships first (product plan
  §9). The `mcp.md` page waits for it.
- **`hsql open`, `hsql history`, "Copy CLI command", the external-command hook** — the rest of
  M4. Only E1 was pulled forward. §3.5's section 9 is the paragraph M4 rewrites.
- **Streaming and pagination** — M5.
- **An agent eval suite** — still out (§5), still the thing most worth adding back.
- **Vector search or an embedding index for the docs.** 55 pages, 116KB. A substring match
  over a prebuilt corpus is the right size of tool; anything more is infrastructure looking for
  a problem.
- **Docs versioning.** Per-release snapshots of the docs site, so an agent can read the docs
  for the version it has installed. Real, and much bigger than this milestone: it changes every
  route. The `manifest.json` version line (§3.4) is the cheap partial answer.
- **Auto-generating adapter pages from each adapter's `--spec`.** It would need every
  out-of-tree adapter installed at site build time, which is the hermetic-build problem again,
  multiplied by twelve.
- **Fixing the site's sidebar API shape** (§1.6's `"/src"`), and the `getting-started/hsql.md`
  brace bug in the *rendered* page. Both are one-line fixes and both are rendering bugs, not
  machine-readability ones; they ride along in PRs 5 and 11 respectively rather than earning
  their own scope.

---

## 8. Corrections to the product plan

Recorded here; applied to `harlequin-for-agents.md` in the same PR as this document.

- **§9's E1 outline uses spellings that do not exist.** `hsql info --json`, `hsql spec --json`
  and `catalog --depth 2` were all written before M2 settled on mode options. The real
  spellings are `hsql --info` and `hsql --spec` (both JSON, always), and `--catalog` with
  `--path`, plus `--catalog-search TERM`. `--depth` was cut from M2 outright (M2 §7), so
  "orient with `catalog --depth 2`" describes a flag that will not exist until
  `fetch_descendants()` does.
- **§9's "Prefer `--read-only`" is conditional on a capability.** Read-only is declared per
  adapter (M2 §3.4), so the skill has to say "where the adapter declares it, which `--info`
  will tell you," not "always."
- **§8's D6 and D7 largely shipped during M1/M2.** The homepage has its two-command section
  and its `#hsql` block; `AGENTS.md` is in both `harlequin` and `harlequin-web`. What is left
  is a one-word correction on the homepage and `AGENTS.md` in the adapter template. The
  roadmap's M3 row overstates the remaining work by two of its seven items.
- **§8's D1 treats `llms-full.txt` as the primary affordance** — "an agent fetches one URL and
  knows everything." Measured, that URL is ~29,000 tokens. `llms.txt` plus per-page `.md` is
  the primary path; `llms-full.txt` is for the caller who genuinely wants the corpus.
- **§8's D3 asks for `updated_at`, which nothing can supply honestly.** There is no date in
  the frontmatter and no git history on the build machine. Drop the field (§6).
- **§8's D2 says "the real work is sanitization" and understates it by one case.** The hard
  one is not the components, it is that `<Figure src={init}>` resolves through an import in the
  `<script>` block the sanitizer is deleting — so the transform has to read the block before it
  removes it (§3.2, steps 2 and 4).
- **§8 does not mention that the site is already a publishing target.** `hsql --config schema`
  has been emitting `$id: https://harlequin.sh/schemas/config/v1.json` since 2.10, and that
  URL 404s. Publishing generated artifacts is a Workstream D deliverable that the roadmap
  never listed (§1.5, §3.4).
- **§8 assumes the docs are correct, and they are not.** Two flags documented in three places
  exit 2, two internal links 404 (§1.4), and the page written for agents renders `&lbrace`
  where `{` belongs (§1.3).
  The workstream's first deliverable is a check, not a corpus — otherwise M3 publishes the
  same errors through four more channels.
