# Harlequin CHANGELOG

All notable changes to this project will be documented in this file.

## [Unreleased]

### Features

-   Double-click or press <kbd>ctrl+enter</kbd> on an item in the data catalog to insert the name in the query editor ([#194](https://github.com/tconbeer/harlequin/issues/194)).

### Bug Fixes and Minor Updates

-   Data table column headers are now bold on terminals that support it ([#203](https://github.com/tconbeer/harlequin/issues/203)).
-   Bumped TextArea; cursor now better maintains x-position and [other minor fixes](https://github.com/tconbeer/textual-textarea/releases/tag/v0.5.4).

## [0.0.28] - 2023-09-07

-   Buffers are now restored when harlequin is restarted ([#175](https://github.com/tconbeer/harlequin/issues/175)).

## [0.0.27] - 2023-08-23

### New Features

-   UI glow-up: Colors are more consistent, and themes set the styling for the entire app ([#81](https://github.com/tconbeer/harlequin/issues/81)). Try `harlequin -t zenburn` or `harlequin -t one-dark` for a new look.
-   Harlequin's query editor now supports more key bindings: <kbd>ctrl+z</kbd> and <kbd>ctrl+y</kbd> to undo/redo, and <kbd>shift+delete</kbd> to delete an entire line.

### Fixes

-   It is now easier to focus on the current editor buffer, instead of the tabs above it.

## [0.0.26] - 2023-08-21

### New Features

-   Harlequin supports multiple buffers (for tabbed editing). Create a new tab with <kbd>ctrl+n</kbd>, close a tab with <kbd>ctrl+w</kbd>, and switch to the next tab with <kbd>ctrl+k</kbd>. Opening, saving, and running queries are operations on the current buffer and have no effect on the other buffers.

## [0.0.25] - 2023-08-13

### New Features

-   Harlequin now returns the result of multiple select queries to different tabs in the Results Viewer. To run multiple queries, type them into the Query Editor (separated by semicolons), then press <kbd>ctrl+a</kbd> to select all, and then <kbd>ctrl+enter</kbd> to run the selection ([#34](https://github.com/tconbeer/harlequin/issues/34)).
-   If there are multiple results tabs, you can switch between them with <kbd>j</kbd> and <kbd>k</kbd>.
-   <kbd>ctrl+e</kbd> exports the data from the current (visible) data table.

### Bug Fixes

-   Fixes issues with the loading state when loading large result sets.

## [0.0.24] - 2023-08-04

### New Features

-   Adds a new CLI option, `--extension` or `-e`, which will install and load a named DuckDB extension.
-   Adds a new CLI option, `--force-install-extensions`, which will re-install the extensions provided
    with the `-e` option.
-   Adds a new CLI option, `--custom-extension-repo`, which enables installing extensions other than
    the official DuckDB extensions.
-   Taken together, Harlequin can now be loaded with the [PRQL](https://github.com/ywelsch/duckdb-prql) extension. Use PRQL with Harlequin:
    ```bash
    harlequin -u -e prql --custom-extension-repo welsch.lu/duckdb/prql/latest
    ```
    ([#152](https://github.com/tconbeer/harlequin/issues/152) - thank you [@dljsjr](https://github.com/dljsjr)!)

## [0.0.23] - 2023-08-03

### Features

-   Changes the behavior of the "Run Query" button and <kbd>ctrl+enter</kbd>:
    -   If text is selected, and that text does not contain parsing errors, the "Run Query" button will show "Run Selection", and <kbd>ctrl+enter</kbd> will run the selected text. If multiple queries are selected (separated by semicolons), they will all be run; if multiple `select` statements are selected, only data from the first selected `select` statement will be loaded into the Results Viewer (or exported).
    -   If no text is selected, Harlequin will run the single query where the cursor is active. Other queries before and after semicolons will not be run.
    -   To "Run All", first select all text with <kbd>ctrl+a</kbd>, and then run selection with <kbd>ctrl+enter</kbd>
-   Adds path autocomplete and validation to the file save/open and export data inputs.

### Other Changes

-   Lowers the maximum number of records loaded into the results viewer to 10,000. (All records can be exported with <kbd>ctrl+e</kbd>)

## [0.0.22] - 2023-08-02

### Features

-   Export data from a query with <kbd>ctrl+e</kbd> ([#149](https://github.com/tconbeer/harlequin/issues/149) - thank you, [@Avsha-Chai](https://github.com/Avsha-Chai)).

## [0.0.21] - 2023-07-28

### Features

-   Add `-u`/`-unsigned`/`--allow-unsigned-extensions` CLI flag for allowing loading of unsigned extensions.
-   File save and open dialog can now expand the user directory (`~`) ([#61](https://github.com/tconbeer/textual-textarea/pull/61))

### Bug Fixes

-   Error modal no longer crashes.
-   Text selection is now maintained when pressing more keys.

## [0.0.20] - 2023-07-17

### Features

-   <kbd>F1</kbd> now displays a help screen that lists all keyboard bindings ([#20](https://github.com/tconbeer/textual-textarea/issues/20)).
-   <kbd>F2</kbd> focuses the keyboard on the query editor.
-   <kbd>F5</kbd> focuses the keyboard on the results viewer.
-   <kbd>F6</kbd> focuses the keyboard on the data catalog.

### Bug Fixes

-   <kbd>ctrl+v</kbd> for paste is now better-supported on all platforms.

## [0.0.19] - 2023-06-26

### Features

-   It's back: select text in the query editor using click and drag ([#42](https://github.com/tconbeer/textual-textarea/issues/42)).

### Bug Fixes

-   Fixes a bug where <kbd>PgUp</kbd> could cause a crash ([#46](https://github.com/tconbeer/textual-textarea/issues/46)).

## [0.0.18] - 2023-06-23

### Bug Fixes

-   Changes format action key binding from <kbd>ctrl+\`</kbd> to <kbd>F4</kbd>. The original binding was causing compatibility
    issues with Windows Powershell and Command Prompt ([#82](https://github.com/tconbeer/harlequin/issues/82)).
-   Adds key binding <kbd>F9</kbd> as an alternative to <kbd>ctrl+b</kbd> to hide the left-hand panel.
-   Fixed query editor scrollbar color to match other widgets ([#109](https://github.com/tconbeer/harlequin/issues/109))
-   Fixed compatibility with Textual v0.28.0 ([#115](https://github.com/tconbeer/harlequin/issues/115))

## [0.0.17] - 2023-06-23

### Features

-   Supports MotherDuck! `harlequin md:` connects to your MotherDuck instance. Optionally pass token with `--md_token <token>` and set SaaS mode with `--md_saas`.

### Bug Fixes

-   Fixes issues with mouse input and focus by rolling back textual_textarea to v0.2.2

## [0.0.16] - 2023-06-20

-   Press <kbd>F10</kbd> with either the Query Editor or Results Viewer in focus to enter "full-screen" mode for those widgets (and hide the other widgets). ([#100](https://github.com/tconbeer/harlequin/issues/100))
-   Select text in the query editor using click and drag ([textual-textarea/#8](https://github.com/tconbeer/textual-textarea/issues/8))

## [0.0.15] - 2023-06-17

-   Adds checkbox for Limit with a configurable input ([#35](https://github.com/tconbeer/harlequin/issues/35)).
-   Adds more obvious Run Query button ([#76](https://github.com/tconbeer/harlequin/issues/76)).
-   Press <kbd>ctrl+b</kbd> to toggle (hide/show) the Data Catalog sidebar. ([#29](https://github.com/tconbeer/harlequin/issues/29), [#103](https://github.com/tconbeer/harlequin/issues/103))
-   Removes the Header for more working space.

## [0.0.14] - 2023-06-15

### Features

-   The schema viewer (now called Data Catalog) now supports multiple databases.
    ([#89](https://github.com/tconbeer/harlequin/issues/89) - thank you
    [@ywelsch](https://github.com/ywelsch)!)
-   Harlequin can be opened with multiple databases by passing them as CLI args:
    `harlequin f1.db iris.db`. Databases can also be attached or detached using
    SQL executed in Harlequin.

### Bug Fixes

-   Reimplements <kbd>ctrl+\`</kbd> to format files (regression from 0.0.13)
-   Updates textual_textarea, which fixes two bugs when opening files
    and another bug related to scrolling the TextArea.

## [0.0.13] - 2023-06-15

### Features

-   Harlequin accepts a new argument, `-t/--theme` to set the Pygments theme for the query editor.
-   Harlequin uses the system clipboard for copying and pasting queries.

### Under the hood

-   Refactors to use the new [tconbeer/textual-textarea](https://github.com/tconbeer/textual-textarea) package.

## [0.0.12] - 2023-05-31

-   improves documentation of <kbd>ctrl+j</kbd> as an alternative key binding for running a query ([#71](https://github.com/tconbeer/harlequin/issues/71) - thank you [@carteakey](https://github.com/carteakey)!)

## [0.0.11] - 2023-05-18

-   adds a command-line option (`-r`, `-readonly`, or `--read-only`) for opening
    the database file in read-only mode.
-   after a query is executed and the data is loaded, the focus shifts to the data table.

## [0.0.10] - 2023-05-17

-   upgrades duckdb to v0.8.0, which includes some breaking changes around types. Harlequin can no longer support earlier versions of duckdb.

## [0.0.9] - 2023-05-16

-   fixes an issue where a DuckDB Error could cause Harlequin to crash ([#56](https://github.com/tconbeer/harlequin/issues/56) - thank you [@Mause](https://github.com/Mause)!)
-   removes docker builds (app UX was poor in a container)

## [0.0.8] - 2023-05-15

-   Cut, copy, paste in text editor with <kbd>ctrl+x</kbd>, <kbd>ctrl+c</kbd>, <kbd>ctrl+u/ctrl+v</kbd>
-   Quit with <kbd>ctrl+q</kbd>, instead of <kbd>ctrl+c</kbd>
-   <kbd>tab</kbd> indents selected text or inserts four-ish spaces in text editor; <kbd>shift+tab</kbd> dedents selected text
-   scroll up and down with <kbd>ctrl+up</kbd> and <kbd>ctrl+down</kbd>
-   fixes an issue where an extra space would be added to the end of lines when pressing <kbd>enter</kbd> in some situations.

## [0.0.7] - 2023-05-12

-   Comment selected text with <kbd>ctrl+/</kbd>
-   Smarter indentation after pressing <kbd>enter</kbd>

## [0.0.6] - 2023-05-09

-   Select text in the query editor using <kbd>shift</kbd> and arrow keys, etc. Replace/delete/quote selection, etc.
-   Improves behavior of inserting opening brackets in the query editor.
-   Hopefully fixes Docker build

## [0.0.5] - 2023-05-08

-   Adds column types to the column header in the results viewer.
-   Text editor now handles <kbd>page up/dn</kbd> and <kbd>ctrl+right/left</kbd> keys.
-   Fixes compatibility with all Pythons >= 3.8

## [0.0.4] - 2023-05-05

-   All-new text area for query editing, with syntax highlighting, scrolling, and more.
-   Loading states and progress bars for long-running queries. Better async use to maintain responsiveness.
-   Fixed edge cases around empty and repeated queries.

## [0.0.3] - 2023-05-04

-   Queries now run asynchronously.
-   Errors from DuckDB are now handled and shown in a pop-up.
-   View columns and data types in the schema viewer sidebar.
-   Queries can be formatted using <kbd>ctrl+\`</kbd>.
-   Queries can be saved using <kbd>ctrl+s</kbd> and opened (loaded) using <kbd>ctrl+o</kbd>.

## [0.0.2] - 2023-05-02

-   View the schema of a DuckDB database in the sidebar.
-   Run queries and view the results.

## [0.0.1] - 2023-05-02

-   Use the DuckDB CLI.

[Unreleased]: https://github.com/tconbeer/harlequin/compare/0.0.28...HEAD

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
