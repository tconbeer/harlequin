# AGENTS.md

This file provides guidance to coding agents (Claude Code, and others that read `AGENTS.md`) when working with code in this repository.

## What this is

Harlequin is a SQL IDE that runs in the terminal, built on [Textual](https://textual.textualize.io/). It connects to databases through pluggable **adapters**; this repo contains the core app plus the two adapters that ship with it (DuckDB, SQLite) and one keymap plugin (VS Code).

User-facing docs live in a separate repo, [`tconbeer/harlequin-web`](https://github.com/tconbeer/harlequin-web) (published at harlequin.sh). Doc changes for a feature go there, not here.

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

## Testing notes

Functional tests drive the real Textual app via `pilot` and assert on both messages and SVG snapshots.

- **A snapshot mismatch is not automatically a failure.** What matters is whether the test passes. Several tests deliberately skip their snapshot assertion (e.g. the `transaction_button_visible` fixture, because SQLite on 3.12+ grows a transaction button that isn't in the baseline), and CI runs with `--snapshot-warn-unused`.
- Snapshots are committed from **Python 3.10**, the lowest supported version. Regenerating requires two runs, and `tests/conftest.py::pytest_configure` will refuse a run that would clobber the baseline:

```bash
uv run pytest --snapshot-update                                                   # on 3.10
uv run --python 3.12 --group test pytest -m 'py12 and not online' --snapshot-update  # py12-only snaps
```

- Async tests need `@pytest.mark.asyncio`. Await `wait_for_workers(app)` (fixture) rather than sleeping — it skips the catalog background loader, which never finishes on its own.
- Shared fixtures: `tests/conftest.py` builds throwaway DuckDB/SQLite databases (`tiny_*`, `small_*`) and app instances (`app`, `app_all_adapters`, `app_small_duck`, …); `app_all_adapters` is parametrized so one test body covers both bundled adapters.

## Import hygiene

The headless CLI (`hsql`, planned in `docs/plans/`) has to reach a database without paying for the TUI, so **the adapter-facing modules must stay free of Textual, questionary, prompt_toolkit and sqlfmt at import time.** Concretely:

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

`plugins.py` loads both groups via `importlib.metadata.entry_points`; a plugin that fails to import prints a warning instead of taking down the app.

### The execution core (`statements.py`, `query.py`)

Both front ends run queries through here, and neither may grow its own copy.

`statements.split()` and `statements.find_separators()` are the **only** SQL splitter. They drive `tree-sitter-sql` directly — no Textual, no textual-textarea — through the one-line `(";" @semicolon)` query the Query Editor has always used, so `-f script.sql` and the editor cannot disagree about where a statement ends. Tree-sitter reports **byte** columns; everything this module returns is in characters, and that conversion belongs here and nowhere else.

`query.execute()` runs statements and `query.fetch()` drains one cursor into a `ResultSet`. Keep them two phases: that split is what lets the app say "query executed" before data materializes. `fetch()` normalizes through `textual_fastdatatable.create_backend()` — a second normalizer would put "what counts as a row, what counts as null" in two places, and the disagreement would show up as two front ends rendering the same query differently.

`RowLimit` carries `detect_overflow` because Harlequin has two different limits: the app's `--limit` is a *soft display cap over a full fetch*, so it knows the exact total; a headless caller wants the *hard* one, and can only learn it was truncated by asking for one row more than it keeps.

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

Config files are TOML, discovered in order home → user config dir → cwd, merged so **later wins**; `pyproject.toml` is read from its `[tool.harlequin]` section. Profiles supply defaults that CLI options override.

### Caches

`editor_cache.py` (open buffers) and `catalog_cache.py` (catalog, query history, S3 tree) pickle into the platformdirs user cache dir, versioned by a `CACHE_VERSION` constant — bump it when the pickled shape changes. Cache entries are keyed by a connection hash derived from `HarlequinAdapter.connection_id`.

### Other

- `stubs/` holds type stubs for untyped dependencies (`mypy_path = "stubs,src"`).
- Styles are Textual CSS: `global.tcss`, `app.tcss`, `keys_app.tcss`.
- `packaging/hsql/` is a metapackage reserving the `hsql` name on PyPI; `docs/plans/` holds design docs for the planned headless CLI.
- `scripts/` has the screenshot exporter used for marketing SVGs and pyinstrument profiling entrypoints (`make profiles`).

## Conventions

- Python 3.10 is the floor (`target-version = "py310"`). Use `from __future__ import annotations`; mypy runs strict, so every def is annotated.
- Textual and its component libraries (`textual-textarea`, `textual-fastdatatable`) are pinned exactly — bump them together, and expect snapshot churn.
- **Every PR adds a `CHANGELOG.md` entry under `[Unreleased]`**, referencing the issue it closes. Keep-a-changelog headings: Features, Performance, Bug Fixes, Dependencies, Refactoring. Releases are cut by the `release.yml` workflow, which bumps the version and rolls `[Unreleased]` into a version heading — don't hand-edit released sections or `version` in `pyproject.toml`.
- CI runs the suite on 3.10–3.14 across Linux, macOS, and Windows; Windows retries flaky tests (`--force-flaky --max-runs=3`).
