# Harlequin CHANGELOG

All notable changes to this project will be documented in this file.

## [Unreleased]

## [2.5.1] - 2025-12-19

### Bug Fixes

- Improves Python 3.14 support; re-enables databricks extra for Python 3.14 users; cleans up duckdb dependency for 3.14 users ([#882](https://github.com/tconbeer/harlequin/issues/882), [alexmalins/harlequin-databricks#23](https://github.com/alexmalins/harlequin-databricks/issues/23) - thank you [@alexmalins](https://github.com/alexmalins) for the Databricks fix!).

## [2.5.0] - 2025-12-17

- Allows setting the config path using the `HARLEQUIN_CONFIG_PATH` environment variable ([#897](https://github.com/tconbeer/harlequin/issues/897))

## [2.4.1] - 2025-10-30

### Bug Fixes

- Fixes a bug that was preventing the Database Catalog from being populated on Python 3.12+ ([#883](https://github.com/tconbeer/harlequin/issues/883) - thank you [@alex-lucem](https://github.com/alex-lucem)!).

## [2.4.0] - 2025-10-29

### Features

- Adds support for Python 3.14 for most Harlequin users ([#852](https://github.com/tconbeer/harlequin/issues/852), [#879](https://github.com/tconbeer/harlequin/pull/879), [#880](https://github.com/tconbeer/harlequin/pull/880) - thank you [@smartinussen](https://github.com/smartinussen) and especially [@branchvincent](https://github.com/branchvincent)!). **NOTE:** The `databricks` extra will not install the Databricks adapter on Python 3.14; Databricks users should continue to use Python 3.10-3.13. uv makes that easy with `uv tool install --python 3.13 'harlequin[databricks]'` See [this issue](https://github.com/alexmalins/harlequin-databricks/issues/23) for more information.

## [2.3.0] - 2025-10-24

### Breaking Changes

- Drops support for Python 3.9.

### Bug Fixes

- Harlequin will no longer execute an empty query; the Run Query button will appear disabled if the buffer is empty ([#873](https://github.com/tconbeer/harlequin/issues/873)).

## [2.2.1] - 2025-10-16

### Bug Fixes

- Fixes installation from an sdist, which was broken in 2.2.0 ([#863](https://github.com/tconbeer/harlequin/issues/863) - thank you [@xhochy](https://github.com/xhochy)!).

## [2.2.0] - 2025-10-16

### Features

- Adds a "Debug Information" screen to make it possible to view Harlequin and Adapter config from within Harlequin by pressing `f12` ([#564](https://github.com/tconbeer/harlequin/discussions/564), [#807](https://github.com/tconbeer/harlequin/pull/807) - thank you, [@vkhitrin](https://github.com/vkhitrin)!).

### Development

- This project is now built using [uv](https://docs.astral.sh/uv/) instead of Poetry. If you are just a user of Harlequin, this should make no difference -- you can still find this project on PyPI and install it however you like (although we recommend uv for that also).

## [2.1.3] - 2025-09-12

### Bug Fixes

- Prevents a large number of `UserWarning` messages from being raised by Click (when multiple adapters are installed) by pinning Click to an earlier version that didn't show such warnings ([#829](https://github.com/tconbeer/harlequin/issues/829) - thank you, [@dusktreader](https://github.com/dusktreader)!).

## [2.1.2] - 2025-04-17

### Bug Fixes

- Fixes a bug in `harlequin --config` where connection strings containing spaces were improperly split into multiple strings ([#800](https://github.com/tconbeer/harlequin/issues/800) - thank you [@Bento-HS](https://github.com/Bento-HS)!).
- Fixes a few bugs with headers for exported data: Harlequin will now use the column name as the export column name for all adapters; CSV export now respects setting the header option to False; and datasets with repeated column names can now be exported ([#779](https://github.com/tconbeer/harlequin/issues/779)).

## [2.1.1] - 2025-03-17

### Bug Fixes

- Fixes a crash caused by a missing `FILE_ICON` property of the S3Tree ([#786](https://github.com/tconbeer/harlequin/issues/786) - thank you [@nyc-de](https://github.com/nyc-de)!).

## [2.1.0] - 2025-03-10

### Extras

- Adds back the `nebulagraph` extra (thank you [@wey-gu](https://github.com/wey-gu)!)

## [2.0.5] - 2025-02-13

### Bug Fixes

- Pins the tree-sitter and tree-sitter-sql versions to avoid a crash caused by a mismatch between those versions.

## [2.0.4] - 2025-02-11

### Bug Fixes

- Harlequin now uses a SQL parser to split the text editor's contents into distinct statements; this fixes a bug where queries could not contain a string literal with a semicolon (like `';'`) ([#348](https://github.com/tconbeer/harlequin/issues/348)).

## [2.0.3] - 2025-02-07

### Dependencies

- Updates the `numpy` dependency pin to make it more likely that `uv tool install harlequin` never builds numpy from source ([#754](https://github.com/tconbeer/harlequin/issues/754)).

## [2.0.2] - 2025-02-07

### Bug Fixes

- Harlequin now supports `infinity` and `-infinity` timestamps (from Postgres and DuckDB), as well as other timestamps that may have previously overflowed Python's native types and been shown as `null` ([#690](https://github.com/tconbeer/harlequin/issues/690) - thank you [@yrashk](https://github.com/yrashk)!).
- Harlequin will no longer show a traceback for exceptions that occur after App shutdown has started ([#745](https://github.com/tconbeer/harlequin/issues/745)).
- Fixes a crash on Windows at start-up due to `NoMatches` on the `ContentSwitcher` ([#742](https://github.com/tconbeer/harlequin/issues/742)).
- Harlequin once again uses the latest version of the IANA TZDATA database for its Windows installations ([#662](https://github.com/tconbeer/harlequin/issues/662)).

## [2.0.1] - 2025-01-29

### Extras

- Adds back the `cassandra` extra (thank you [@vkhitrin](https://github.com/vkhitrin)!)

## [2.0.0] - 2025-01-07

### Breaking Changes

- Drops support for Python 3.8.
- Drops support for Pygments themes in favor of Textual themes. Use `harlequin --config` to update your config files with a new theme. The default theme, `harlequin`, remains unchanged.
- Removes the `cassandra` and `nebulagraph` extras, due to package compatibility issues.

### Features

- Adds fuzzy matching for autocomplete ([#671](https://github.com/tconbeer/harlequin/pull/671)).
- Adds support for Python 3.13.

## [1.25.2] - 2024-10-31

### Bug Fixes

- Fixes a bug where string data was rendered as Rich Markup ([#647](https://github.com/tconbeer/harlequin/issues/647) - thank you [@burncitiesburn](https://github.com/burncitiesburn)!).
- Fixes a bug where `None` could be inconsistently displayed as `"None"` or the correct `∅ null` ([#658](https://github.com/tconbeer/harlequin/issues/658), [#655](https://github.com/tconbeer/harlequin/issues/655) - thank you, [@sgpeter1](https://github.com/sgpeter1)!).
- `harlequin --config` now supports the `NO_COLOR` environment variable (the rest of the app already supported it) ([#552](https://github.com/tconbeer/harlequin/issues/552) - thank you [@glujan](https://github.com/glujan)!).

## [1.25.1] - 2024-10-31

### Bug Fixes

- Fixes a hang and crash caused by an upstream bug in rendering zero-width containers ([#659](https://github.com/tconbeer/harlequin/issues/659), [#668](https://github.com/tconbeer/harlequin/issues/668), [#672](https://github.com/tconbeer/harlequin/issues/672)).

## [1.25.0] - 2024-10-09

### Features

- Harlequin's Data Catalog is now interactive! Adapters can define interactions on catalog nodes, which can be selected via a context menu by right-clicking on nodes in the context menu or pressing `.` (this binding is configurable via the `data_catalog.show_context_menu` action) ([#213](https://github.com/tconbeer/harlequin/issues/213)).
- For adapters that support it, Harlequin's Data Catalog now loads lazily. This should dramatically improve Data Catalog load time for catalogs with thousands of nodes. The Data Catalog is no longer cached ([#491](https://github.com/tconbeer/harlequin/issues/491) - thank you [@rymurr](https://github.com/rymurr)!).
- The DuckDB and SQLite adapters now support lazy-loading and a wide range of interactions, including `use`ing database and schemas, previewing data, dropping objects, and showing DDL for objects.

### Bug Fixes

- Fixes a crash on Windows caused by automatically downloading the 2024b tzdata file from IANA, which includes a single poorly-formatted date that breaks PyArrow's upstream tzdata parser ([#652](https://github.com/tconbeer/harlequin/issues/652) - thank you [@paulrobello](https://github.com/paulrobello) and [@alexmalins](https://github.com/alexmalins)!).
- Fixes a crash caused by pressing `ctrl+g` when the goto input was already open ([#654](https://github.com/tconbeer/harlequin/issues/654)).
- Fixes a crash caused by copying or pasting on certain systems where Pyperclip doesn't properly catch and reraise errors ([#649](https://github.com/tconbeer/harlequin/issues/649)).

## [1.24.1] - 2024-09-25

### Bug Fixes

- DuckDB & SQLite adapters: fix bug to properly resolve initialization script paths starting with `~` (i.e. user's home dir) supplied to `--init-path` ([#646](https://github.com/tconbeer/harlequin/pull/646)).

## [1.24.0] - 2024-08-19

### Features

- For adapters that support canceling queries, Harlequin will now display a "Cancel Query" button while queries are running.
- Two new Actions are available for key bindings: `run_query`  (with a global app scope, in addition to the existing `code_editor.run_query` action) and `cancel_query`.
- Adapters may now implement a `connection_id` property to improve Harlequin's ability to persist the data catalog and query history across Harlequin invocations ([#410](https://github.com/tconbeer/harlequin/issues/410)).
- Adapters may now implement a `HarlequinConnection.cancel()` method to cancel all in-flight queries ([#333](https://github.com/tconbeer/harlequin/issues/333)).
- Queries can now be canceled when using the DuckDB or SQLite adapters.

### Bug Fixes

- Harlequin no longer constrains the version of the adapter when the adapter is installed as an extra; Harlequin will install the latest available version that does not conflict with other dependencies.
- The DuckDB and SQLite adapters now use only the resolved file path to the database file to load the data catalog and query history from disk (ignoring other connection options). This should improve cache hits. They will no longer attempt to load a cached catalog or history for in-memory connections.

## [1.23.2] - 2024-08-15

### Bug Fixes

- Fixes an issue where uncommenting a line using the uncomment action could indent the line by an additional space ([#616](https://github.com/tconbeer/harlequin/issues/616) - thank you [@harrymconner](https://github.com/harrymconner)!).
- The (empty) results viewer will no longer be focused after executing only DDL queries (queries that do not return any data) ([#609](https://github.com/tconbeer/harlequin/issues/609)).

## [1.23.1] - 2024-07-23

### Bug Fixes

- Harlequin no longer crashes when attempting to display negative datetime values ([#568](https://github.com/tconbeer/harlequin/issues/568)). 

## [1.23.0] - 2024-07-11

### Features

- Harlequin now supports an additional [theme](https://harlequin.sh/docs/themes), [`coffee`](https://pygments.org/styles/#coffee).

### Changes

- Harlequin's Footer has been re-designed. In the footer, `CTRL+` key presses are now represented by a carat, `^`. For example, instead of `CTRL+Q Quit` the footer now reads `^q Quit`.
- The tooltip for overflowing data cells has been improved to better format the data contained in the cell.

### Bug Fixes

- Fixed a bug where the main panel would resize while the code editor was being mounted at app start-up.
- Fixed a bug that could cause a crash if data table cell contents contained a string that could be interpreted as bad Rich Markup ([#569](https://github.com/tconbeer/harlequin/issues/569) - thank you [@cmdkev](https://github.com/cmdkev)!)

## [1.22.2] - 2024-07-09

### Bug Fixes

- Improves support for system clipboard on Wayland ([#585](https://github.com/tconbeer/harlequin/issues/585) - thank you [@SalmanFarooqShiekh](https://github.com/SalmanFarooqShiekh)!).

## [1.22.1] - 2024-06-28

### Bug Fixes

- Harlequin no longer hard-codes the `ctrl+q` binding to the Quit action (previously this could not be changed with a keymap). If your keymap does not define a binding for Quit, Harlequin will use `ctrl+q` so that you can always exit the app, even with a bad keymap.

## [1.22.0] - 2024-06-27

### Features

- Harlequin now loads key bindings from keymap plug-ins, and accepts a `--keymap_name` CLI option to specify a keymap to be loaded. This option can be repeated to load (and merge) multiple keymaps.
- Harlequin now also loads key bindings from keymaps configured in Harlequin config files or `pyproject.toml` files. To merge user-defined keymaps with keymap plug-ins, repeat the `--keymap-name` option. For example: `--keymap-name vscode --keymap-name my_custom_keymap`. For more information on user-defined keymaps, see Harlequin's [docs](https://harlequin.sh/docs/bindings).
- Harlequin ships with a new app for creating keymaps to customize keybindings. You can run it with `harlequin --keys`. The new app will load any existing keymap config and allow you to edit individual bindings. On quitting the app, it will write the new keymap to a file, so you can use it the next time you start Harlequin.

### Changed

- The default key bindings have been refactored to a plug-in in a separate package (`harlequin_vscode`) that is distributed with Harlequin.

## [1.21.0] - 2024-06-17

- The Cassandra adapter is now installable as an extra; use `pip install harlequin[cassandra]`.
- The NebulaGraph adapter is now installable as an extra; use `pip install harlequin[nebulagraph]`.

## [1.20.0] - 2024-04-29

### Features

- For adapters that support it, Harlequin now provides a buttons to toggle the transaction mode of the database connection, and commit and roll back transactions ([#334](https://github.com/tconbeer/harlequin/issues/334)).
- Adapters can now implement `HarlequinConnection.transaction_mode` and `HarlequinConnection.toggle_transaction_mode()` to enable the new Transaction Mode UI for their adapter.

### Changed

- SQLite adapter: The adapter no longer accepts an `--isolation-level` option on Python 3.12 or higher; instead, the adapter allows autocommit configuration via the Harlequin UI.

## [1.19.0] - 2024-04-25

### Features

- SQLite adapter: Harlequin now executes an initialization script on start-up of the SQLite adapter. By default, it executes the script found at `~/.sqliterc`. To execute a different script, start Harlequin with the `--init-path` option (aliases `-i`/`-init`):

  ```bash
  harlequin -a sqlite --init-path ./my-project-script
  ```

  To start Harlequin without executing an initialization script, use the `--no-init` flag:

  ```bash
  harlequin -a sqlite --no-init
  ```

  **Note:** SQLite initialization scripts can contain dot commands or SQL statements. If Harlequin encounters a dot command, it will attempt to rewrite it as a SQL statement, and then execute the rewritten statement. Otherwise, it will ignore the dot command. Currently, Harlequin can only rewrite `.open` and `.load` commands.

  ([#325](https://github.com/tconbeer/harlequin/issues/325))

- SQLite adapter: Adds a new CLI option, `--extension` or `-e`, which will load a SQLite extension. **Note:** SQLite extensions are not supported by Python on most platforms by default. See [here](https://harlequin.sh/docs/sqlite/extensions) for more details ([#533](https://github.com/tconbeer/harlequin/issues/533)).

## [1.18.0] - 2024-04-19

### Features

- The Query Editor's Open and Save dialogs now display the full computed file path that will be opened or saved ([tconbeer/textual-textarea#232](https://github.com/tconbeer/textual-textarea/issues/232) - thank you [@bjornasm](https://github.com/bjornasm)!)
- The Query Editor adds a "Find" action with <kbd>ctrl+f</kbd> and "Find Next" action with <kbd>F3</kbd>.
- The Query Editor adds a "Go To Line" action with <kbd>ctrl+g</kbd>.
- The Query Editor adds bindings for <kbd>ctrl+shift+home/end</kbd> to select text while moving the cursor to the start/end of the document.

### Bug Fixes

- Fixes a crash from initializing the Error Modal incorrectly from the Query Editor.
- Fixes a crash from saving to a path in a non-existent directory.

### Changes

- The Query Editor uses a slightly different implementation of undo and redo, with improved performance and some subtly different behavior ([#240](https://github.com/tconbeer/textual-textarea/issues/240).

## [1.17.0] - 2024-04-16

### Features

- A new `HarlequinConnection.close()` method can be implemented by adapters to gracefully close database connections when the application exits.
- The ADBC adapter is now installable as an extra; use `pip install harlequin[adbc]`.

### Bug Fixes

- Fixes broken link on clipboard error message ([#509](https://github.com/tconbeer/harlequin/issues/509))

## [1.16.2] - 2024-03-29

### Bug Fixes

- If the cursor is after the final semicolon in the query editor, and there is only whitespace after the semicolon, Harlequin will now execute the last query before the semicolon, instead of doing nothing when clicking Run Query or pressing <kbd>ctrl+j</kbd>.

## [1.16.1] - 2024-03-27

### Bug Fixes

- Pressing `F8` on the history screen no longer causes a crash ([#485](https://github.com/tconbeer/harlequin/issues/485))

## [1.16.0] - 2024-02-22

### Changes

- The default search path and priority for config files has changed, to better align with the standard defined by each operating system. Harlequin now loads config files from the following locations (and merges them, with items listed first taking priority):
  1. The file located at the path provided by the `--config-path` CLI option.
  2. Files named `harlequin.toml`, `.harlequin.toml`, or `pyproject.toml` in the current working directory.
  3. Files named `harlequin.toml`, `.harlequin.toml`, or `config.toml` in the user's default config directory, in the `harlequin` subdirectory. For example:
     - Linux: `$XDG_CONFIG_HOME/harlequin/config.toml` or `~/.config/harlequin/config.toml`
     - Mac: `~/Library/Application Support/harlequin/config.toml`
     - Windows: `~\AppData\Local\harlequin\config.toml`
  4. Files named `harlequin.toml`, `.harlequin.toml`, or `pyproject.toml` in the user's home directory (`~`).
     ([#471](https://github.com/tconbeer/harlequin/issues/471))

### Features

- `harlequin --config` option now accepts the `--config-path` CLI option ([#466](https://github.com/tconbeer/harlequin/issues/466)).
- `harlequin --config` now defaults to updating the nearest (highest priority) existing config file in the default search path, instead of `./.harlequin.toml`.

### Bug Fixes

- `harlequin --config` creates a new file (parent folder as well, if non-existent) instead of crashing with FileNotFoundError ([#465](https://github.com/tconbeer/harlequin/issues/465))

## [1.15.0] - 2024-02-12

### Features

- The Data Exporter has been refactored to work with any adapter.
- The Data Exporter now supports two additional formats: Feather and ORC (ORC is not supported on Windows).

### Bug Fixes

- The Query Editor no longer loses focus after pressing `escape` (regression since 1.14.0).

## [1.14.0] - 2024-02-07

### Features

- The Databricks adapter is now installable as an extra; use `pip install harlequin[databricks]`. Thank you [@alexmalins](https://github.com/alexmalins)!
- In the Results Viewer, values are now formatted based on their type. Numbers have separators based on the locale, and numbers, dates/times/etc., and bools are right-aligned. Null values are now shown as a dim `∅ null`, instead of a blank cell.
- Adds a `--locale` option to override the system locale for number formatting.

### Bug Fixes

- The result counts in the Query History view now contain thousands separators ([#437](https://github.com/tconbeer/harlequin/issues/437) - thank you, [@code-master-ajay](https://github.com/code-master-ajay)!).
- Harlequin no longer crashes when executing SQLite queries that return multiple types in a single column ([#453](https://github.com/tconbeer/harlequin/issues/453)).

### Performance

- Harlequin now starts much faster, especially when restoring multiple buffers from the cache.

## [1.13.0] - 2024-01-26

### Features

- Adds a Query History Viewer: press <kbd>F8</kbd> to view a list of up to 500 previously-executed queries ([#259](https://github.com/tconbeer/harlequin/issues/259)).

### Bug Fixes

- The new `--show-files` and `--show-s3` options are now correctly grouped under "Harlequin Options" in `harlequin --help`; installed adapters are now alphabetically sorted.

## [1.12.0] - 2024-01-22

### Features

- Adds an option, `--show-files` (alias `-f`), which will display the passed directory in the Data Catalog, alongside the connected database schema, in a second tab. Like database catalog items, you can use <kbd>ctrl+enter</kbd>, <kbd>ctrl+j</kbd>, or double-click to insert the path into the query editor.
- Adds an option, `--show-s3` (alias `--s3`), which will display objects from the passed URI in the Data Catalog (in another tab). Uses the credentials from the AWS CLI's default profile. Use `--show-s3 all` to show all objects in all buckets for the currently-authenticated user, or pass buckets and key prefixes to restrict the catalog. For example, these all work:
  ```bash
  harlequin --show-s3 my-bucket
  harlequin --show-s3 my-bucket/my-nested/key-prefix
  harlequin --show-s3 s3://my-bucket
  harlequin --show-s3 https://my-storage.com/my-bucket/my-prefix
  harlequin --show-s3 https://my-bucket.s3.amazonaws.com/my-prefix
  harlequin --show-s3 https://my-bucket.storage.googleapis.com/my-prefix
  ```
- Items in the Data Catalog can now be copied to the clipboard with <kbd>ctrl+c</kbd>.

## [1.11.0] - 2024-01-12

### Features

- Harlequin now shows a more helpful error message when attempting to open a sqlite file with the duckdb adapter or vice versa ([#401](https://github.com/tconbeer/harlequin/issues/401)).
- <kbd>ctrl+r</kbd> forces a refresh of the Data Catalog (the catalog is automatically refreshed after DDL queries are executed in Harlequin) ([#375](https://github.com/tconbeer/harlequin/issues/375)).
- At startup, Harlequin attempts to load a cached version of the Data Catalog. The Data Catalog will be updated in the background. A loading indicator will be displayed if there is no cached catalog for the connection parameters ([#397](https://github.com/tconbeer/harlequin/issues/397)).

### Bug Fixes

- The Data Catalog no longer shows the loading state after an error loading the catalog.
- Harlequin now exits if attempting to open an invalid file with the sqlite adapter.

## [1.10.0] - 2024-01-11

### Features

- Harlequin now loads immediately and connects to your database in the background ([#393](https://github.com/tconbeer/harlequin/issues/393)).
- Harlequin shows a loading indicator before the Data Catalog is hydrated for the first time ([#396](https://github.com/tconbeer/harlequin/issues/396)).

### Bug Fixes

- Fixes a bug where `harlequin --config` would crash if configuring an adapter that declared no options.

## [1.9.2] - 2024-01-10

### Features

- The ODBC adapter is now installable as an extra; use `pip install harlequin[odbc]`.

## [1.9.1] - 2024-01-09

### Bug Fixes

- Improves compatibility for adapter return types to accept a sequence of any iterable ([tconbeer/textual-fastdatatable#68](https://github.com/tconbeer/textual-fastdatatable/pull/68)).

## [1.9.0] - 2024-01-08

### Features

- Improves keyboard navigation of the Results Viewer by adding key bindings, including <kbd>ctrl+right/left/up/down/home/end</kbd>, <kbd>tab</kbd>, and <kbd>ctrl+a</kbd>.
- The Trino adapter is now installable as an extra; use `pip install harlequin[trino]`.
- Harlequin will automatically download a missing timezone database on Windows. Prevent this behavior with `--no-download-tzdata`.

### Bug Fixes

- Fixes a crash when selecting data from a timestamptz field ([#382](https://github.com/tconbeer/harlequin/issues/382)) (or another field with an invalid Arrow data type).

## [1.8.0] - 2023-12-21

### Features

- Select a range of cells in the Results Viewer by clicking and dragging or by holding <kbd>shift</kbd> while moving the cursor with the keyboard.
- Copy selected cells from the Results Viewer by pressing <kbd>ctrl+c</kbd>.
- Very long values in the Results Viewer are now truncated, with an elipsis (`…`). The full value is shown in a tooltip when hovering over a truncated value. (The full value will also be copied to the clipboard).
- The BigQuery adapter is now installable as an extra; use `pip install harlequin[bigquery]`.

### Bug Fixes

- Fixes an issue on Windows where pressing <kbd>shift</kbd> or <kbd>ctrl</kbd> would hide the member autocomplete menu.
- Fixes flaky query execution behavior on some platforms.

### Testing

- tests/functional_tests/test_app.py has been refactored into many smaller files.
- Fixes an issue with cache tests where the user's main harlequin cache was used instead of a mocked cache location.

## [1.7.3] - 2023-12-15

### Bug Fixes

- Fixes an issue where completions were truncated improperly in the autocomplete menu.

### Testing

- Prevents limit input cursor blink when running tests in headless mode, for less flaky tests.

## [1.7.2] - 2023-12-14

### Features

- The MySQL adapter is now installable as an extra; use `pip install harlequin[mysql]`.

## [1.7.1] - 2023-12-14

### Bug Fixes

- Fixes a crash when using `harlequin-postgres` and executing a select statement that returns zero records.

## [1.7.0] - 2023-12-13

### Features

- AUTOCOMPLETE! Harlequin's query editor will now offer completions in a drop-down for SQL keywords, functions, and database objects (like tables, views, columns). With the autocomplete list open, use <kbd>up</kbd>, <kbd>down</kbd>, <kbd>PgUp</kbd>, <kbd>PgDn</kbd>, to select an option and <kbd>enter</kbd> or <kbd>Tab</kbd> to insert it into the editor.
- Harlequin now uses a new TextArea widget for its code editor. This improves performance for long queries, adds line numbers in a gutter, and changes the underlying engine for syntax highlighting from Pygments to Tree Sitter ([tconbeer/textual-textarea#123](https://github.com/tconbeer/textual-textarea/issues/123)).
- In the Query Editor: double-click to select a word, triple-click to select a line, and quadruple-click to select the entire query ([tconbeer/textual-textarea#111](https://github.com/tconbeer/textual-textarea/issues/111), [tconbeer/textual-textarea#112](https://github.com/tconbeer/textual-textarea/issues/112)).

### Changes

- Changes the default theme to `harlequin`.

### Adapter API Changes

- Many key types are now exported from the main `harlequin` package: `HarlequinAdapter`, `HarlequinConnection`, `HarlequinCursor`, `HarlequinAdapterOption`, `HarlequinCopyFormat`, `HarlequinCompletion`.
- `HarlequinConnection`s may now (optionally) define a `get_completions()` method, which should return a list of `HarlequinCompletion` instances; each returned completion will be available to users in the autocompletion list.

### Bug Fixes

- Fixes a bug that was causing an empty line to appear at the bottom of the Query Editor pane.

## [1.6.0] - 2023-12-07

### Features

- Harlequin can now be configured using a TOML file. The config file can both specify options for Harlequin (like the theme and row limit) and also for installed adapters (like the host, username, and password for a database connection). The config file can define multiple "profiles" (sets of configuration), and you can select the profile to use when starting Harlequin with the `--profile` option (alias `-P`). By default, Harlequin searches the current directory and home directories for files called either `.harlequin.toml` or `pyproject.toml`, and merges the config it finds in them. You can specify a different path using the `--config-path` option. Values loaded from config files can be overridden by passing CLI options ([#206](https://github.com/tconbeer/harlequin/issues/206)).
- Harlequin now ships with a wizard to make it easy to create or update config files. Simply run Harlequin with the `--config` option.
- Adds a `harlequin` theme. You can use it with `harlequin -t harlequin`.

## [1.5.0] - 2023-11-28

### Breaking Changes

- The SQLite adapter no longer provides a `check-same-thread` option; the established connection sets this value to False to enable Harlequin features.

### Features

- The Postgres adapter is now installable as an extra; use `pip install harlequin[postgres]`.

### Bug Fixes

- Harlequin no longer becomes unresponsive when loading a large data catalog or executing long-running queries ([#236](https://github.com/tconbeer/harlequin/issues/236), [#332](https://github.com/tconbeer/harlequin/issues/332), [#331](https://github.com/tconbeer/harlequin/issues/331)).
- Fixes a flaky test that was causing intermittent CI failures.

## [1.4.1] - 2023-11-20

### Bug Fixes

- Adds a `py.typed` file to the `harlequin` package.

## [1.4.0] - 2023-11-18

### Features

- Harlequin now ships with an experimental SQLite adapter and can be used to query any SQLite database (including an in-memory database). You can select the adapter by starting Harlequin with `harlequin -a sqlite` (for an in-memory session) or `harlequin -a sqlite my.db`.
- `harlequin --help` is all-new, with a glow-up provided by [`rich-click`](https://github.com/ewels/rich-click). Options for each adapter are separated into their own panels.
- `harlequin --version` now shows the versions of installed database adapters ([#317](https://github.com/tconbeer/harlequin/issues/317)).

### Refactoring

- The code for the DuckDB adapter has been moved from `/plugins/harlequin_duckdb` to `/src/harlequin_duckdb`.
- The unused `export_options.py` module has been removed ([#327](https://github.com/tconbeer/harlequin/issues/327)).

## [1.3.1] - 2023-11-13

### Bug Fixes

- When running multiple queries, Harlequin now activates the results tab for the last query, instead of the first one.
- Queries that return duplicate column names are now displayed correctly in the Results Viewer ([tconbeer/textual-fastdatatable#26](https://github.com/tconbeer/textual-fastdatatable/issues/26)).
- List types returned by DuckDB no longer display as `?`, but instead as `[#]`, `[s]`, etc. ([#315](https://github.com/tconbeer/harlequin/issues/315)).
- Map types returned by DuckDB now display as `{m}`, to differentiate them from structs (`{}`).
- The Results Viewer no longer displays "Query Returned No Records" before the first query is executed.
- The data returned by HarlequinCursor.fetchall() no longer needs to be a PyArrow Table ([#281](https://github.com/tconbeer/harlequin/issues/281)).

## [1.3.0] - 2023-11-06

### Features

- Adds an `--adapter` CLI option (alias `-a`) for selecting an installed adapter plug-in.

### Bug Fixes

- Fixes a crash that could happen when a query returned no records ([tconbeer/textual-fastdatatable#19](https://github.com/tconbeer/textual-fastdatatable/issues/19)).

### Adapter API Changes

- The function signature for HarlequinConnection.copy() has changed to add a `format_name` positional argument.
- The HarlequinAdapter.COPY_OPTIONS class variable has been renamed to HarlequinAdapter.COPY_FORMATS, and its
  type has changed.
- The function signature for HarlequinAdapter.connect() has changed to return only a HarlequinConnection; HarlequinConnection now accepts an `init_message` kwarg that will be displayed to the user as a notification.

### Refactoring

- Harlequin's CLI now dynamically loads the available options from the installed adapters ([#276](https://github.com/tconbeer/harlequin/issues/276)).
- Harlequin now dynamically loads data export options from the selected adapter ([#275](https://github.com/tconbeer/harlequin/issues/275)).

## [1.2.0] - 2023-10-22

## [1.2.0-alpha.1] - 2023-10-22

### Bug Fixes

- Harlequin's query notifications no longer count whitespace-only queries ([#268](https://github.com/tconbeer/harlequin/issues/268)).
- Harlequin's DataCatalog now displays "db" next to database names and "sch" next to schema names. Empty databases and schemas no longer have an arrow to expand them.
- If the cursor is after the final semicolon in the query editor, Harlequin will now execute the last query before the semicolon, instead of doing nothing when clicking Run Query or pressing <kbd>ctrl+j</kbd>.

### Refactoring

- Harlequin's DuckDB integration has been refactored into a more general-purpose database adapter interface ([#263](https://github.com/tconbeer/harlequin/issues/263)).
- Harlequin's DuckDB adapter is now loaded as a plug-in ([#279](https://github.com/tconbeer/harlequin/issues/279))

## [1.1.1] - 2023-10-09

### Bug Fixes

- Harlequin no longer crashes if the data returned by DuckDB contains NoneType or complex (Struct, Map, List) columns ([#265](https://github.com/tconbeer/harlequin/issues/265) - thank you [@sjdurfey](https://github.com/sjdurfey)!).

### Testing

- Harlequin now uses snapshot testing on screenshots to prevent regresssions ([#252](https://github.com/tconbeer/harlequin/issues/252)).
- Harlequin no longer installs extensions or connects to MotherDuck in CI, due to flaky failures around the time of DuckDB releases ([#262](https://github.com/tconbeer/harlequin/issues/262)).   

## [1.1.0] - 2023-10-02

### Features

- Harlequin now executes an initialization script on start-up. By default, it executes the script found at `~/.duckdbrc`. To execute a different script, start Harlequin with the `--init-path` option:

  ```bash
  harlequin --init-path ./my-project-script.sql
  ```

  To start Harlequin without executing an initialization script, use the `--no-init` flag:

  ```bash
  harlequin --no-init
  ```

  **Note:** DuckDB initialization scripts can contain dot commands or SQL statements. If Harlequin encounters a dot command, it will attempt to rewrite it as a SQL statement, and then execute the rewritten statement. Otherwise, it will ignore the dot command. Currently, Harlequin can only rewrite `.open` commands.

  ([#241](https://github.com/tconbeer/harlequin/issues/241) - thank you [@pdpark](https://github.com/pdpark)!)

- Harlequin now displays notifications after completing successful queries ([#235](https://github.com/tconbeer/harlequin/issues/235) - thank you [@natir](https://github.com/natir)!), saving the contents of a buffer ([#226](https://github.com/tconbeer/harlequin/issues/226)), and receiving an error from the system clipboard.

- Harlequin now loads data from a completed query up to 1,000x faster by using a new DataTable widget ([#181](https://github.com/tconbeer/harlequin/issues/181)). By default, the Results Viewer is now limited to 100,000 records, instead of 10,000. This limit can be changed with the `--limit` option when starting Harlequin. This introduces a dependency on PyArrow >= 7.0.0.

## [1.0.1] - 2023-09-21

### Bug Fixes

- Pasting text into Harlequin's text editor is now more performant and compatible with more terminals. ([#120](https://github.com/tconbeer/textual-textarea/issues/120) - thank you [@matsonj](https://github.com/matsonj), [#119](https://github.com/tconbeer/textual-textarea/issues/119)).

## [1.0.0] - 2023-09-12

### Features

- Double-click or press <kbd>ctrl+enter</kbd> on an item in the data catalog to insert the name in the query editor ([#194](https://github.com/tconbeer/harlequin/issues/194)).
- Harlequin now shows notifications when exporting data or executing DDL/DML.

### Bug Fixes and Minor Updates

- Data table column headers are now bold on terminals and fonts that support it ([#203](https://github.com/tconbeer/harlequin/issues/203)).
- Bumped TextArea; cursor now better maintains x-position and [other minor fixes](https://github.com/tconbeer/textual-textarea/releases/tag/v0.5.4).
- The query editor's cursor no longer blinks when a modal appears above it ([#196](https://github.com/tconbeer/harlequin/issues/196)).
- Harlequin now shows the results of successful queries in the Results Viewer if multiple queries are executed and one or more contain errors.
- Error and Help modals can now be dismissed with a click outside the modal ([#218](https://github.com/tconbeer/harlequin/issues/218)).

## [0.0.28] - 2023-09-07

- Buffers are now restored when Harlequin is restarted ([#175](https://github.com/tconbeer/harlequin/issues/175)).

## [0.0.27] - 2023-08-23

### New Features

- UI glow-up: Colors are more consistent, and themes set the styling for the entire app ([#81](https://github.com/tconbeer/harlequin/issues/81)). Try `harlequin -t zenburn` or `harlequin -t one-dark` for a new look.
- Harlequin's query editor now supports more key bindings: <kbd>ctrl+z</kbd> and <kbd>ctrl+y</kbd> to undo/redo, and <kbd>shift+delete</kbd> to delete an entire line.

### Fixes

- It is now easier to focus on the current editor buffer, instead of the tabs above it.

## [0.0.26] - 2023-08-21

### New Features

- Harlequin supports multiple buffers (for tabbed editing). Create a new tab with <kbd>ctrl+n</kbd>, close a tab with <kbd>ctrl+w</kbd>, and switch to the next tab with <kbd>ctrl+k</kbd>. Opening, saving, and running queries are operations on the current buffer and have no effect on the other buffers.

## [0.0.25] - 2023-08-13

### New Features

- Harlequin now returns the result of multiple select queries to different tabs in the Results Viewer. To run multiple queries, type them into the Query Editor (separated by semicolons), then press <kbd>ctrl+a</kbd> to select all, and then <kbd>ctrl+enter</kbd> to run the selection ([#34](https://github.com/tconbeer/harlequin/issues/34)).
- If there are multiple results tabs, you can switch between them with <kbd>j</kbd> and <kbd>k</kbd>.
- <kbd>ctrl+e</kbd> exports the data from the current (visible) data table.

### Bug Fixes

- Fixes issues with the loading state when loading large result sets.

## [0.0.24] - 2023-08-04

### New Features

- Adds a new CLI option, `--extension` or `-e`, which will install and load a named DuckDB extension.
- Adds a new CLI option, `--force-install-extensions`, which will re-install the extensions provided
  with the `-e` option.
- Adds a new CLI option, `--custom-extension-repo`, which enables installing extensions other than
  the official DuckDB extensions.
- Taken together, Harlequin can now be loaded with the [PRQL](https://github.com/ywelsch/duckdb-prql) extension. Use PRQL with Harlequin:
  ```bash
  harlequin -u -e prql --custom-extension-repo welsch.lu/duckdb/prql/latest
  ```
  ([#152](https://github.com/tconbeer/harlequin/issues/152) - thank you [@dljsjr](https://github.com/dljsjr)!)

## [0.0.23] - 2023-08-03

### Features

- Changes the behavior of the "Run Query" button and <kbd>ctrl+enter</kbd>:
  - If text is selected, and that text does not contain parsing errors, the "Run Query" button will show "Run Selection", and <kbd>ctrl+enter</kbd> will run the selected text. If multiple queries are selected (separated by semicolons), they will all be run; if multiple `select` statements are selected, only data from the first selected `select` statement will be loaded into the Results Viewer (or exported).
  - If no text is selected, Harlequin will run the single query where the cursor is active. Other queries before and after semicolons will not be run.
  - To "Run All", first select all text with <kbd>ctrl+a</kbd>, and then run selection with <kbd>ctrl+enter</kbd>
- Adds path autocomplete and validation to the file save/open and export data inputs.

### Other Changes

- Lowers the maximum number of records loaded into the results viewer to 10,000. (All records can be exported with <kbd>ctrl+e</kbd>)

## [0.0.22] - 2023-08-02

### Features

- Export data from a query with <kbd>ctrl+e</kbd> ([#149](https://github.com/tconbeer/harlequin/issues/149) - thank you, [@Avsha-Chai](https://github.com/Avsha-Chai)).

## [0.0.21] - 2023-07-28

### Features

- Add `-u`/`-unsigned`/`--allow-unsigned-extensions` CLI flag for allowing loading of unsigned extensions.
- File save and open dialog can now expand the user directory (`~`) ([#61](https://github.com/tconbeer/textual-textarea/pull/61))

### Bug Fixes

- Error modal no longer crashes.
- Text selection is now maintained when pressing more keys.

## [0.0.20] - 2023-07-17

### Features

- <kbd>F1</kbd> now displays a help screen that lists all keyboard bindings ([#20](https://github.com/tconbeer/textual-textarea/issues/20)).
- <kbd>F2</kbd> focuses the keyboard on the query editor.
- <kbd>F5</kbd> focuses the keyboard on the results viewer.
- <kbd>F6</kbd> focuses the keyboard on the data catalog.

### Bug Fixes

- <kbd>ctrl+v</kbd> for paste is now better-supported on all platforms.

## [0.0.19] - 2023-06-26

### Features

- It's back: select text in the query editor using click and drag ([#42](https://github.com/tconbeer/textual-textarea/issues/42)).

### Bug Fixes

- Fixes a bug where <kbd>PgUp</kbd> could cause a crash ([#46](https://github.com/tconbeer/textual-textarea/issues/46)).

## [0.0.18] - 2023-06-23

### Bug Fixes

- Changes format action key binding from <kbd>ctrl+\`</kbd> to <kbd>F4</kbd>. The original binding was causing compatibility
  issues with Windows Powershell and Command Prompt ([#82](https://github.com/tconbeer/harlequin/issues/82)).
- Adds key binding <kbd>F9</kbd> as an alternative to <kbd>ctrl+b</kbd> to hide the left-hand panel.
- Fixed query editor scrollbar color to match other widgets ([#109](https://github.com/tconbeer/harlequin/issues/109))
- Fixed compatibility with Textual v0.28.0 ([#115](https://github.com/tconbeer/harlequin/issues/115))

## [0.0.17] - 2023-06-23

### Features

- Supports MotherDuck! `harlequin md:` connects to your MotherDuck instance. Optionally pass token with `--md_token <token>` and set SaaS mode with `--md_saas`.

### Bug Fixes

- Fixes issues with mouse input and focus by rolling back textual_textarea to v0.2.2

## [0.0.16] - 2023-06-20

- Press <kbd>F10</kbd> with either the Query Editor or Results Viewer in focus to enter "full-screen" mode for those widgets (and hide the other widgets). ([#100](https://github.com/tconbeer/harlequin/issues/100))
- Select text in the query editor using click and drag ([textual-textarea/#8](https://github.com/tconbeer/textual-textarea/issues/8))

## [0.0.15] - 2023-06-17

- Adds checkbox for Limit with a configurable input ([#35](https://github.com/tconbeer/harlequin/issues/35)).
- Adds more obvious Run Query button ([#76](https://github.com/tconbeer/harlequin/issues/76)).
- Press <kbd>ctrl+b</kbd> to toggle (hide/show) the Data Catalog sidebar. ([#29](https://github.com/tconbeer/harlequin/issues/29), [#103](https://github.com/tconbeer/harlequin/issues/103))
- Removes the Header for more working space.

## [0.0.14] - 2023-06-15

### Features

- The schema viewer (now called Data Catalog) now supports multiple databases.
  ([#89](https://github.com/tconbeer/harlequin/issues/89) - thank you
  [@ywelsch](https://github.com/ywelsch)!)
- Harlequin can be opened with multiple databases by passing them as CLI args:
  `harlequin f1.db iris.db`. Databases can also be attached or detached using
  SQL executed in Harlequin.

### Bug Fixes

- Reimplements <kbd>ctrl+\`</kbd> to format files (regression from 0.0.13)
- Updates textual_textarea, which fixes two bugs when opening files
  and another bug related to scrolling the TextArea.

## [0.0.13] - 2023-06-15

### Features

- Harlequin accepts a new argument, `-t/--theme` to set the Pygments theme for the query editor.
- Harlequin uses the system clipboard for copying and pasting queries.

### Under the hood

- Refactors to use the new [tconbeer/textual-textarea](https://github.com/tconbeer/textual-textarea) package.

## [0.0.12] - 2023-05-31

- improves documentation of <kbd>ctrl+j</kbd> as an alternative key binding for running a query ([#71](https://github.com/tconbeer/harlequin/issues/71) - thank you [@carteakey](https://github.com/carteakey)!)

## [0.0.11] - 2023-05-18

- adds a command-line option (`-r`, `-readonly`, or `--read-only`) for opening
  the database file in read-only mode.
- after a query is executed and the data is loaded, the focus shifts to the data table.

## [0.0.10] - 2023-05-17

- upgrades duckdb to v0.8.0, which includes some breaking changes around types. Harlequin can no longer support earlier versions of duckdb.

## [0.0.9] - 2023-05-16

- fixes an issue where a DuckDB Error could cause Harlequin to crash ([#56](https://github.com/tconbeer/harlequin/issues/56) - thank you [@Mause](https://github.com/Mause)!)
- removes docker builds (app UX was poor in a container)

## [0.0.8] - 2023-05-15

- Cut, copy, paste in text editor with <kbd>ctrl+x</kbd>, <kbd>ctrl+c</kbd>, <kbd>ctrl+u/ctrl+v</kbd>
- Quit with <kbd>ctrl+q</kbd>, instead of <kbd>ctrl+c</kbd>
- <kbd>tab</kbd> indents selected text or inserts four-ish spaces in text editor; <kbd>shift+tab</kbd> dedents selected text
- scroll up and down with <kbd>ctrl+up</kbd> and <kbd>ctrl+down</kbd>
- fixes an issue where an extra space would be added to the end of lines when pressing <kbd>enter</kbd> in some situations.

## [0.0.7] - 2023-05-12

- Comment selected text with <kbd>ctrl+/</kbd>
- Smarter indentation after pressing <kbd>enter</kbd>

## [0.0.6] - 2023-05-09

- Select text in the query editor using <kbd>shift</kbd> and arrow keys, etc. Replace/delete/quote selection, etc.
- Improves behavior of inserting opening brackets in the query editor.
- Hopefully fixes Docker build

## [0.0.5] - 2023-05-08

- Adds column types to the column header in the results viewer.
- Text editor now handles <kbd>page up/dn</kbd> and <kbd>ctrl+right/left</kbd> keys.
- Fixes compatibility with all Pythons >= 3.8

## [0.0.4] - 2023-05-05

- All-new text area for query editing, with syntax highlighting, scrolling, and more.
- Loading states and progress bars for long-running queries. Better async use to maintain responsiveness.
- Fixed edge cases around empty and repeated queries.

## [0.0.3] - 2023-05-04

- Queries now run asynchronously.
- Errors from DuckDB are now handled and shown in a pop-up.
- View columns and data types in the schema viewer sidebar.
- Queries can be formatted using <kbd>ctrl+\`</kbd>.
- Queries can be saved using <kbd>ctrl+s</kbd> and opened (loaded) using <kbd>ctrl+o</kbd>.

## [0.0.2] - 2023-05-02

- View the schema of a DuckDB database in the sidebar.
- Run queries and view the results.

## [0.0.1] - 2023-05-02

- Use the DuckDB CLI.

[unreleased]: https://github.com/tconbeer/harlequin/compare/2.5.1...HEAD
[2.5.1]: https://github.com/tconbeer/harlequin/compare/2.5.0...2.5.1
[2.5.0]: https://github.com/tconbeer/harlequin/compare/2.4.1...2.5.0
[2.4.1]: https://github.com/tconbeer/harlequin/compare/2.4.0...2.4.1
[2.4.0]: https://github.com/tconbeer/harlequin/compare/2.3.0...2.4.0
[2.3.0]: https://github.com/tconbeer/harlequin/compare/2.2.1...2.3.0
[2.2.1]: https://github.com/tconbeer/harlequin/compare/2.2.0...2.2.1
[2.2.0]: https://github.com/tconbeer/harlequin/compare/2.1.3...2.2.0
[2.1.3]: https://github.com/tconbeer/harlequin/compare/2.1.2...2.1.3
[2.1.2]: https://github.com/tconbeer/harlequin/compare/2.1.1...2.1.2
[2.1.1]: https://github.com/tconbeer/harlequin/compare/2.1.0...2.1.1
[2.1.0]: https://github.com/tconbeer/harlequin/compare/2.0.5...2.1.0
[2.0.5]: https://github.com/tconbeer/harlequin/compare/2.0.4...2.0.5
[2.0.4]: https://github.com/tconbeer/harlequin/compare/2.0.3...2.0.4
[2.0.3]: https://github.com/tconbeer/harlequin/compare/2.0.2...2.0.3
[2.0.2]: https://github.com/tconbeer/harlequin/compare/2.0.1...2.0.2
[2.0.1]: https://github.com/tconbeer/harlequin/compare/2.0.0...2.0.1
[2.0.0]: https://github.com/tconbeer/harlequin/compare/1.25.2...2.0.0
[1.25.2]: https://github.com/tconbeer/harlequin/compare/1.25.1...1.25.2
[1.25.1]: https://github.com/tconbeer/harlequin/compare/1.25.0...1.25.1
[1.25.0]: https://github.com/tconbeer/harlequin/compare/1.24.1...1.25.0
[1.24.1]: https://github.com/tconbeer/harlequin/compare/1.24.0...1.24.1
[1.24.0]: https://github.com/tconbeer/harlequin/compare/1.23.2...1.24.0
[1.23.2]: https://github.com/tconbeer/harlequin/compare/1.23.1...1.23.2
[1.23.1]: https://github.com/tconbeer/harlequin/compare/1.23.0...1.23.1
[1.23.0]: https://github.com/tconbeer/harlequin/compare/1.22.2...1.23.0
[1.22.2]: https://github.com/tconbeer/harlequin/compare/1.22.1...1.22.2
[1.22.1]: https://github.com/tconbeer/harlequin/compare/1.22.0...1.22.1
[1.22.0]: https://github.com/tconbeer/harlequin/compare/1.21.0...1.22.0
[1.21.0]: https://github.com/tconbeer/harlequin/compare/1.20.0...1.21.0
[1.20.0]: https://github.com/tconbeer/harlequin/compare/1.19.0...1.20.0
[1.19.0]: https://github.com/tconbeer/harlequin/compare/1.18.0...1.19.0
[1.18.0]: https://github.com/tconbeer/harlequin/compare/1.17.0...1.18.0
[1.17.0]: https://github.com/tconbeer/harlequin/compare/1.16.2...1.17.0
[1.16.2]: https://github.com/tconbeer/harlequin/compare/1.16.1...1.16.2
[1.16.1]: https://github.com/tconbeer/harlequin/compare/1.16.0...1.16.1
[1.16.0]: https://github.com/tconbeer/harlequin/compare/1.15.0...1.16.0
[1.15.0]: https://github.com/tconbeer/harlequin/compare/1.14.0...1.15.0
[1.14.0]: https://github.com/tconbeer/harlequin/compare/1.13.0...1.14.0
[1.13.0]: https://github.com/tconbeer/harlequin/compare/1.12.0...1.13.0
[1.12.0]: https://github.com/tconbeer/harlequin/compare/1.11.0...1.12.0
[1.11.0]: https://github.com/tconbeer/harlequin/compare/1.10.0...1.11.0
[1.10.0]: https://github.com/tconbeer/harlequin/compare/1.9.2...1.10.0
[1.9.2]: https://github.com/tconbeer/harlequin/compare/1.9.1...1.9.2
[1.9.1]: https://github.com/tconbeer/harlequin/compare/1.9.0...1.9.1
[1.9.0]: https://github.com/tconbeer/harlequin/compare/1.8.0...1.9.0
[1.8.0]: https://github.com/tconbeer/harlequin/compare/1.7.3...1.8.0
[1.7.3]: https://github.com/tconbeer/harlequin/compare/1.7.2...1.7.3
[1.7.2]: https://github.com/tconbeer/harlequin/compare/1.7.1...1.7.2
[1.7.1]: https://github.com/tconbeer/harlequin/compare/1.7.0...1.7.1
[1.7.0]: https://github.com/tconbeer/harlequin/compare/1.6.0...1.7.0
[1.6.0]: https://github.com/tconbeer/harlequin/compare/1.5.0...1.6.0
[1.5.0]: https://github.com/tconbeer/harlequin/compare/1.4.1...1.5.0
[1.4.1]: https://github.com/tconbeer/harlequin/compare/1.4.0...1.4.1
[1.4.0]: https://github.com/tconbeer/harlequin/compare/1.3.1...1.4.0
[1.3.1]: https://github.com/tconbeer/harlequin/compare/1.3.0...1.3.1
[1.3.0]: https://github.com/tconbeer/harlequin/compare/1.2.0...1.3.0
[1.2.0]: https://github.com/tconbeer/harlequin/compare/1.2.0-alpha.1...1.2.0
[1.2.0-alpha.1]: https://github.com/tconbeer/harlequin/compare/1.1.1...1.2.0-alpha.1
[1.1.1]: https://github.com/tconbeer/harlequin/compare/1.1.0...1.1.1
[1.1.0]: https://github.com/tconbeer/harlequin/compare/1.0.1...1.1.0
[1.0.1]: https://github.com/tconbeer/harlequin/compare/1.0.0...1.0.1
[1.0.0]: https://github.com/tconbeer/harlequin/compare/0.0.28...1.0.0
[0.0.28]: https://github.com/tconbeer/harlequin/compare/0.0.26...0.0.28
[0.0.26]: https://github.com/tconbeer/harlequin/compare/0.0.25...0.0.26
[0.0.25]: https://github.com/tconbeer/harlequin/compare/0.0.24...0.0.25
[0.0.24]: https://github.com/tconbeer/harlequin/compare/0.0.23...0.0.24
[0.0.23]: https://github.com/tconbeer/harlequin/compare/0.0.22...0.0.23
[0.0.22]: https://github.com/tconbeer/harlequin/compare/0.0.21...0.0.22
[0.0.21]: https://github.com/tconbeer/harlequin/compare/0.0.20...0.0.21
[0.0.20]: https://github.com/tconbeer/harlequin/compare/0.0.19...0.0.20
[0.0.19]: https://github.com/tconbeer/harlequin/compare/0.0.18...0.0.19
[0.0.18]: https://github.com/tconbeer/harlequin/compare/0.0.17...0.0.18
[0.0.17]: https://github.com/tconbeer/harlequin/compare/0.0.16...0.0.17
[0.0.16]: https://github.com/tconbeer/harlequin/compare/0.0.15...0.0.16
[0.0.15]: https://github.com/tconbeer/harlequin/compare/0.0.14...0.0.15
[0.0.14]: https://github.com/tconbeer/harlequin/compare/0.0.13...0.0.14
[0.0.13]: https://github.com/tconbeer/harlequin/compare/0.0.12...0.0.13
[0.0.12]: https://github.com/tconbeer/harlequin/compare/0.0.11...0.0.12
[0.0.11]: https://github.com/tconbeer/harlequin/compare/0.0.10...0.0.11
[0.0.10]: https://github.com/tconbeer/harlequin/compare/0.0.9...0.0.10
[0.0.9]: https://github.com/tconbeer/harlequin/compare/0.0.8...0.0.9
[0.0.8]: https://github.com/tconbeer/harlequin/compare/0.0.7...0.0.8
[0.0.7]: https://github.com/tconbeer/harlequin/compare/0.0.6...0.0.7
[0.0.6]: https://github.com/tconbeer/harlequin/compare/0.0.5...0.0.6
[0.0.5]: https://github.com/tconbeer/harlequin/compare/0.0.4...0.0.5
[0.0.4]: https://github.com/tconbeer/harlequin/compare/0.0.3...0.0.4
[0.0.3]: https://github.com/tconbeer/harlequin/compare/0.0.2...0.0.3
[0.0.2]: https://github.com/tconbeer/harlequin/compare/0.0.1...0.0.2
[0.0.1]: https://github.com/tconbeer/harlequin/compare/39e26b6dda462cd430eda69daf5ef7157dac4da6...0.0.1
