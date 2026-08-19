# AGENTS.md

This file provides guidance to coding agents (Claude Code, and others that read `AGENTS.md`) when working with code in this repository.

## What this is

Harlequin is a SQL IDE that runs in the terminal, built on [Textual](https://textual.textualize.io/). It connects to databases through pluggable **adapters**; this repo contains the core app plus the two adapters that ship with it (DuckDB, SQLite) and one keymap plugin (VS Code).

User-facing docs live in a separate repo, [`tconbeer/harlequin-web`](https://github.com/tconbeer/harlequin-web) (published at harlequin.sh). Doc changes for a feature go there, not here.

## We own most of the stack — fix things upstream

Much of what Harlequin depends on is maintained in the same org, so a limitation in a dependency is usually not something to work around:

- **`textual-fastdatatable`** and **`textual-textarea`**, the component libraries the Results Viewer and Query Editor are built on.
- **`pytest-textual-snapshot`**, pinned to a fork (see the `test` dependency group).
- **Several adapters** — `harlequin_duckdb` and `harlequin_sqlite` in this repo, plus out-of-tree ones like `harlequin-postgres` and `harlequin-mysql`.

**When the real fix belongs upstream, make it upstream.** A workaround here is a permanent tax on this repo for a problem whose actual home is one release away, and it will be read by the next person as intended design. If you can't reach the upstream repo yourself, write the change up as an issue and hand it over — don't quietly absorb it.

Worked example: `create_backend()` had no way to accept the column names a cursor reported, so a result with no rows arrived with no header. The reconciliation that would otherwise have lived in `harlequin.query` forever — special cases for `None`, for an empty sequence, for `f0`/`f1` names, for a count mismatch — became a `column_names` argument in textual-fastdatatable 0.17.0 and one line here.

The costs are real and worth planning around rather than avoiding: an upstream fix needs a release and a pin bump before this repo sees it, and a component-library bump can bring snapshot churn. Neither outweighs owning a workaround forever.

## Commands

Everything runs through `uv`. `make check` is the full pre-PR loop; run it before pushing.

```bash
make check      # sync deps, ruff format+fix, pytest (3.10 + 3.12 py12 tests), mypy
make lint       # ruff format + ruff check --fix + mypy only
make serve      # run the app against ./f1.db in textual dev mode
make sqlite     # run the app with the sqlite adapter, dev mode
make keys       # run the `harlequin --keys` keymap editor, dev mode
```

Individual tools:

```bash
uv run pytest -m "not online"                      # the standard test run
uv run pytest tests/functional_tests/test_app.py::test_select_1 -n0   # single test
uv run mypy                                        # strict; covers src/ and tests/
uv run ruff format . && uv run ruff check . --fix
uv run harlequin [OPTIONS] [CONN_STR]              # run the CLI from source
```

- Tests run under xdist (`-n auto` is in `addopts`). Pass `-n0` when using a debugger or `-s`.
- Markers: `online` (needs network + secrets; always deselect locally), `py12` (only runs on 3.12+), `use_cache` (opts a test back into the buffer/catalog caches that an autouse fixture otherwise disables).
- `TEST_MARKERS` in the Makefile is a marker *expression*, not a flag — a second `-m` silently overrides the first.
- **On a fresh Linux container, generate the `en_US.UTF-8` locale before the first test run.** A session-scoped autouse fixture (`tests/conftest.py::set_locale_to_enUS`) calls `set_locale("en_US.UTF-8")`, and an image that ships only `C`/`POSIX` (check with `locale -a`) errors every test in setup with `locale.Error: unsupported locale setting`. One command fixes it, and it is not a test failure to debug:

```bash
localedef -i en_US -f UTF-8 en_US.UTF-8   # prefix with sudo if you are not root
```

## Testing notes

Functional tests drive the real Textual app via `pilot` and assert on both messages and SVG snapshots. Unit tests use syrupy directly for the headless output formats (`tests/unit_tests/test_golden_formats.py`), as single-file binary snapshots — same `--snapshot-update` workflow, and `.gitattributes` pins every `__snapshots__` file to LF so a Windows checkout can't rewrite what they assert.

- **A snapshot mismatch is not automatically a failure.** What matters is whether the test passes. Several tests deliberately skip their snapshot assertion (e.g. the `transaction_button_visible` fixture, because SQLite on 3.12+ grows a transaction button that isn't in the baseline), and CI runs with `--snapshot-warn-unused`.
- Snapshots are committed from **Python 3.10**, the lowest supported version. Regenerating requires two runs, and `tests/conftest.py::pytest_configure` will refuse a run that would clobber the baseline:

```bash
uv run pytest --snapshot-update                                                   # on 3.10
uv run --python 3.12 --group test pytest -m 'py12 and not online' --snapshot-update  # py12-only snaps
```

- Async tests need `@pytest.mark.asyncio`. Await `wait_for_workers(app)` (fixture) rather than sleeping — it skips the catalog background loader, which never finishes on its own.
- Shared fixtures: `tests/conftest.py` builds throwaway DuckDB/SQLite databases (`tiny_*`, `small_*`) and app instances (`app`, `app_all_adapters`, `app_small_duck`, …); `app_all_adapters` is parametrized so one test body covers both bundled adapters.

## Import hygiene

The headless CLI (`hsql`) has to reach a database without paying for the TUI, so **the adapter-facing modules must stay free of Textual, questionary, prompt_toolkit and sqlfmt at import time.** Concretely:

- `harlequin/__init__.py` resolves its public names through a PEP 562 `__getattr__`. Never add a module-scope import there — importing *any* `harlequin.*` submodule executes it, so one eager import puts the whole app behind every consumer.
- Type-only imports of Textual (e.g. `AutoBackendType`) go under `if TYPE_CHECKING:`; every module involved already has `from __future__ import annotations`.
- Renderer imports in `options.py` (`questionary`, `harlequin.colors`, `harlequin.copy_widgets`) are deferred into `to_widgets()` / `to_questionary()`. Declaring an option stays cheap; rendering one pays.
- **stdout belongs to query output.** Diagnostics — including plug-in load failures and `pretty_print_warning` — go to stderr, or they contaminate piped output.

Two guards, and they check different things:

```bash
uv run lint-imports                 # import-linter contracts in pyproject.toml
uv run pytest tests/unit_tests/test_import_hygiene.py    # what actually happens at run time
uv run python scripts/cold_start.py # informational: ms and module count per import
```

import-linter reads the *static* graph, so it cannot tell a deferred import from a module-scope one — the deliberate deferrals are listed in `ignore_imports` with a comment each. The subprocess tests are what prove a deferral actually defers, so a new deferral needs an entry in both places.

## Architecture

### Packages

`src/` holds four installable packages, all built into the one wheel:

- `harlequin` — the app, CLI, config, plugin loading, and the adapter/catalog ABCs that adapters implement.
- `harlequin_duckdb`, `harlequin_sqlite` — in-tree adapters, registered through the `harlequin.adapter` entry point group like any third-party adapter. They are the reference implementations of the adapter contract.
- `harlequin_vscode` — a keymap, registered through `harlequin.keymap`.

`plugins.py` loads both groups via `importlib.metadata.entry_points`; a plugin that fails to import prints a warning instead of taking down the app. Loading comes at three grains, because `ep.load()` is the most expensive thing a front end can do at start-up and it grows with every adapter the user installs. `adapter_names()` reads entry point *names* and imports nothing; `load_adapter(name)` imports exactly one, and raises rather than warning, since a caller that named an adapter has nothing to fall back to; `load_adapter_plugins()` imports every one, for the `harlequin` command, whose `--help` describes them all.

### The execution core (`statements.py`, `query.py`)

Both front ends run queries through here, and neither may grow its own copy.

`statements.split()` and `statements.find_separators()` are the **only** SQL splitter. They drive `tree-sitter-sql` directly — no Textual, no textual-textarea — through the one-line `(";" @semicolon)` query the Query Editor has always used, so `-f script.sql` and the editor cannot disagree about where a statement ends. Tree-sitter reports **byte** columns; everything this module returns is in characters, and that conversion belongs here and nowhere else.

`fetch()` hands `create_backend()` the columns the cursor described, so a result with no rows is an empty table with a header rather than nothing — `ResultSet.backend` is never None.

**Two things de-duplicate column names, and they have to agree.** Arrow allows `select 1 as a, 2 as a` and `to_pylist()` silently drops the second one, so `create_backend()` resolves duplicates for `backend.data` while `source_data` keeps them verbatim. duckdb won't export duplicates either, so `export.write_file()` resolves them too — for the app, which exports `source_data`. Both paths must produce the same header for the same query, so `export._deduplicate_column_names()` is character-for-character what the backend does, pinned by a test that asserts against the backend rather than against a copy of its algorithm. If upstream changes the scheme, follow it here rather than forking.

`query.execute()` runs statements and `query.fetch()` drains one cursor into a `ResultSet`. Keep them two phases: that split is what lets the app say "query executed" before data materializes. `fetch()` normalizes through `textual_fastdatatable.create_backend()` — a second normalizer would put "what counts as a row, what counts as null" in two places, and the disagreement would show up as two front ends rendering the same query differently.

`ResultSet.arrow_table()` is the rows a result *holds*, under the column names the cursor described — the backend renames what it can't normalize, and an adapter that returns tuples arrives with `f0`, `f1`, …. `text_columns()` is that table cast to VARCHAR **in duckdb**, which is where the text layouts get their strings; never `str()`, and never `textual_fastdatatable`'s `cell_formatter`, which is display formatting (locale-grouped numbers, `✓ True`) and would put `1,234,567` in a numeric column.

**There are two kinds of limit, and every option is exactly one of them.** `RowLimit` is the *hard* one — `cursor.set_limit()`, so fewer rows leave the database — and it is what `limit` means in both commands: in the IDE it arrives as the Run Query Bar's limit, which `--limit` fills in and checks. It carries `detect_overflow` because a hard limit makes the true total unknowable: `set_limit(n)` then `fetchall()` returns at most n rows and says nothing about an n+1th, so asking for one row more is the only way to learn it was cut short. The *soft* caps are applied over rows already fetched, so the total stays exact: `viewer_max_rows` (`fetch(display_limit=…)`) for the Results Viewer, and `LayoutOptions.max_rows` for the text layouts, each with a default per layout. `ResultSet` holds the pair: `row_count` is what it kept, `fetched_row_count` is what the database returned (probe row excluded), and `truncated` says whether there were more. `-1` means unlimited in every option that takes rows; `0` means zero rows, except for the viewer's cap, where it has always meant unlimited.

### Output (`export.py`, `layout.py`)

**duckdb serializes; Harlequin lays out.** `export.write_file()` / `write_stream()` take an Arrow table to a path or an open binary stream, and every value in them is rendered by duckdb's own writers — so a Postgres blob and a DuckDB blob print identically, and nothing here has to own a rendering for intervals, structs or maps. `tsv`, `jsonl`/`ndjson` and `arrow` are the csv, json and feather writers under different default options, not new writers; a caller's explicit option always beats the format's default.

`layout.py` does padding, pipes and row counts over the strings `text_columns()` produced, and knows nothing about types. Widths are **terminal cells, via `wcwidth`** — never `len()`, which is off by one per CJK glyph or emoji and by one the other way per combining mark. Its `LayoutOptions` are independent switches on purpose: `-t` is `header=False, footer=False` and `-A` is `aligned=False`, so `-tA` needs no special case.

Two invariants the tests pin: **the bytes are the contract** — writers go through a temp file and are copied out in binary, so `\n` survives on Windows and `-o PATH` and `> PATH` agree — and **`--format table` and `--format csv` agree cell for cell**, which is what the output snapshots in `tests/unit_tests/__snapshots__/test_golden_formats/` exist to catch. They are syrupy single-file snapshots, one per format, written in binary — regenerate them with `--snapshot-update` on 3.10 like every other snapshot here, and read the diff; a change there is a change to Harlequin's output contract.

### The headless CLI (`hsql/`)

`hsql` is a second console script over the same execution core, not a mode of `harlequin`. It owns no query logic: `cli.py` parses, `harlequin.query` runs, `harlequin.layout` and `harlequin.export` write. New query behavior belongs in the core so both front ends get it.

- `cli.py` parses in **two phases**. The first reads `-a`, `-P` and `--config-path` with a throwaway resilient parser to name the one adapter the invocation will use — `adapter_names()`, no `ep.load()` — and the second builds the real command with only that adapter's options. `--help` with no adapter named imports *nothing* and lists adapter names instead; that's what keeps the first thing an agent reads small and true for every adapter. `harlequin.query` and `harlequin.statements` are deferred into the callback for the same reason.
- **hsql's own flags are the frozen part.** An adapter option whose spelling collides with one loses that spelling (`_attach_adapter_options`), rather than shadowing a documented flag.
- `diagnostics.py` owns **everything on stderr and the exit-code mapping**. Nothing else in `hsql/` writes to stderr, and nothing writes to stdout except `output.py`.
- `output.py` picks between the two families of format and writes through **one binary stream**, `-o PATH` included, so a file and a redirect cannot disagree about a byte.
- **The two commands name each other, out of copied lists.** `harlequin.cli.HSQL_ONLY_OPTIONS` is every spelling `hsql` has and the IDE does not, so `harlequin -c` can point at `hsql -c`; `diagnostics.IDE_THEMES` is every name `harlequin -t` takes, so `hsql -t nord` can say what it did with `nord`. Copies rather than lookups in both directions — `hsql` may not reach `harlequin.colors`, and the IDE should not import `hsql` to render an error — and each is pinned by a test that compares it to the real thing.
- Two things it must never do: call `set_locale()` (output that varies with `LC_ALL` is output a caller cannot predict) or reach `harlequin.cli` (that module builds the IDE's command). Both are covered by the `hsql does not reach the TUI` import-linter contract and `tests/unit_tests/test_hsql.py`.

### The adapter contract (`adapter.py`, `catalog.py`, `driver.py`)

`HarlequinAdapter` (built from CLI/config options) → `connect()` → `HarlequinConnection` (execute, get_catalog, optional copy/cancel/transaction-mode/completions) → `HarlequinCursor` (columns, set_limit, fetchall). Adapters must tolerate receiving both subsets and supersets of their declared options, and must not rely on option defaults.

The catalog is a tree of `CatalogItem`s. `InteractiveCatalogItem` subclasses add `fetch_children()` for lazy loading and an `INTERACTIONS` class var for the right-click context menu. Interactions run on worker threads, so they never touch widgets — they call `HarlequinDriver` methods, which post messages back to the app (insert text, confirm-and-execute, notify, refresh catalog).

Changing anything in `adapter.py`, `catalog.py`, or `driver.py` is a public API change for every out-of-tree adapter.

### App and threading model (`app.py`)

`Harlequin(AppBase)` composes `DataCatalog | (EditorCollection, RunQueryBar, ResultsViewer)` plus a footer. The invariant to preserve: **all database work happens in `@work(thread=True, exclusive=True, exit_on_error=False, group=...)` workers that never mutate widgets.** Workers `post_message(...)`; `@on(...)` handlers on `Harlequin` own all state changes. The query path is `QuerySubmitted → _execute_query → QueriesExecuted → (fetch) → ResultsFetched`, with parallel flows for `DatabaseConnected`, `NewCatalog`/`NewCatalogItems`, `CompletersReady`, and `TransactionModeChanged`.

Both query workers are thin wrappers over the execution core above: `_execute_query` calls `query.execute()`, `_fetch_data` calls `query.fetch()`. New query behavior belongs in the core, where `hsql` can reach it, not in the worker.

Adapters are third-party code: a raw driver exception (not a `HarlequinQueryError`) must surface as an error modal, never crash the app.

The Data Catalog (`components/data_catalog/database_tree.py`) loads by viewport, not eagerly: an `asyncio.PriorityQueue` feeds a background loader, with user-expanded nodes at `DEMAND_PRIORITY` jumping ahead of speculative `PREFETCH_PRIORITY` work near the viewport, and children added in chunks so a wide node can't stall rendering.

### Keys and actions (`actions.py`, `keymap.py`, `bindings.py`, `keys_app.py`)

`HARLEQUIN_ACTIONS` is the single registry mapping an action name to a target widget class and an action method. Keymaps (plugin- or config-defined) map keys to those names, and `bind()` applies them at runtime. A new user-bindable behavior needs an entry in `HARLEQUIN_ACTIONS` — otherwise it can't be bound or shown in the help screen. `harlequin --keys` launches a second Textual app (`keys_app.py`) that edits keymaps and writes them back into the user's config file.

### Options and config (`options.py`, `config.py`, `cli.py`)

`AbstractOption` subclasses (`TextOption`, `ListOption`, `PathOption`, `SelectOption`, `FlagOption`) each know how to render themselves three ways: `to_click()` for the CLI, `to_widgets()` for the TUI, and `to_questionary()` for the config wizard. Declaring an adapter option once gets all three. `cli.py` builds the click command dynamically from the loaded adapters' `ADAPTER_OPTIONS`.

Config files are TOML, read in priority order — an explicit `--config-path`, then cwd → user config dir → home — and merged so **the nearest file wins**, per profile and per keymap rather than per top-level table: a project-local file that defines one profile leaves the rest of them, and the `default_profile` naming one of them, alone. `pyproject.toml` is read from its `[tool.harlequin]` section. Profiles supply defaults that CLI options override.

`config.py`'s module docstring has the table of which command calls which loader and how many files it reads; the short version is that `load_profile()` stops at the file that defines the profile it was asked for, and `load_config()` is the whole document. Resolving a name is separate from reading one: a `default_profile` that names no profile is raised by `_select_profile()`, where the name is used, so `-P other` is not refused over a key it overrode — which is what keeps the two commands agreeing wherever they read the same files. They still differ where they do not: `hsql` cannot report a problem in a file it stopped before opening.

Validation is two passes, both msgspec, and they know different things. `Config` is the TypedDict *and* the model, so `msgspec.convert(raw, Config)` is the whole of a file's shape check, per file and before the merge, so an error can name the file. `parse_profile_options()` runs once the adapter is known: it builds a struct from that adapter's `ADAPTER_OPTIONS` and parses the profile's remaining keys into it, which is the only place in the stack that can tell `reed_only` from `read_only` — an adapter's constructor takes supersets of what it declares and drops the rest. Values arrive as the declared type (`port = 5432` becomes `"5432"`), because a profile writes what TOML makes natural. Each command passes the names it reads for itself: its own click params, plus `TUI_ONLY_KEYS` (in `hsql`) or `hsql_profile_keys()` (in the IDE, which reads them off `hsql.cli.bare_command()` — built without an adapter or a config file, ~10ms — rather than keeping a copy). msgspec is imported at module scope, because `Config` is a `Struct`: that costs ~13ms on an invocation that finds no config file at all, and is what makes `forbid_unknown_fields` the thing that refuses a key nobody declared.

That last sentence is `config.merge_profile_with_cli()`, and it belongs to every command rather than to `cli.py`: **a CLI value beats the profile only if the user actually typed it**, since an option sitting at its default carries no intent and would otherwise clobber what the profile set. It takes the *names* of the options that were set — in click, every parameter whose `ctx.get_parameter_source()` isn't `DEFAULT` — rather than a `Context`, so the rule is testable without building a command. An empty `conn_str` is the documented exception: it's an argument, so click always reports it as coming from the command line.

### Caches

`editor_cache.py` (open buffers) and `catalog_cache.py` (catalog, query history, S3 tree) pickle into the platformdirs user cache dir, versioned by a `CACHE_VERSION` constant — bump it when the pickled shape changes. Cache entries are keyed by a connection hash derived from `HarlequinAdapter.connection_id`.

### Other

- `stubs/` holds type stubs for untyped dependencies (`mypy_path = "stubs,src"`).
- Styles are Textual CSS: `global.tcss`, `app.tcss`, `keys_app.tcss`.
- `packaging/hsql/` is a metapackage reserving the `hsql` name on PyPI; `docs/plans/` holds the design docs behind the headless CLI.
- `scripts/` has the screenshot exporter used for marketing SVGs and pyinstrument profiling entrypoints (`make profiles`).

## Conventions

- **Docstrings and comments are as concise as possible.** One sentence describing the function, and at most a sentence or two on why it works the way it does. **Explain what the code does and why — nothing else.** They are not the place to relay the design behind a change, the alternatives weighed, or the instructions the author was working from — that belongs in the PR description, or in `docs/plans/` for anything longer-lived. Specifically:
  - **Never reference code that was removed or refactored.** A comment saying "X rather than the old Y" reads to the next person as a live distinction; the diff is where a change is visible, and it stops being visible the moment the PR merges.
  - **Never record a decision, a tradeoff, or feedback from a PR comment.** "We chose A over B", "as discussed in review", and "keep this until #123 lands" all belong in the PR, the issue, or `docs/plans/`.
- Python 3.10 is the floor (`target-version = "py310"`). Use `from __future__ import annotations`; mypy runs strict, so every def is annotated.
- Textual and its component libraries (`textual-textarea`, `textual-fastdatatable`) are pinned exactly — bump them together, and expect snapshot churn.
- **Every PR should consider adding a `CHANGELOG.md` entry under `[Unreleased]`**, referencing the issue it closes. Only include changes that are NOTABLE for Harlequin users! Do not include implementation details: Harlequin users do not care! Just address user-facing bugs and enhancements. **Keep entries short and to the point: one or two sentences per feature, saying what was added and how to use it.** If an entry is explaining how something works inside, cut it. Keep-a-changelog headings: Features, Performance, Bug Fixes, Dependencies, Refactoring. Releases are cut by the `release.yml` workflow, which bumps the version and rolls `[Unreleased]` into a version heading — don't hand-edit released sections or `version` in `pyproject.toml`.
- CI runs the suite on 3.10–3.14 across Linux, macOS, and Windows; Windows retries flaky tests (`--force-flaky --max-runs=3`).
