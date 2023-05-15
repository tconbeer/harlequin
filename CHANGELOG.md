# Harlequin CHANGELOG

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.0.8] - 2023-05-15

-   Cut, copy, paste in text editor with `ctrl+x`, `ctrl+c`, `ctrl+u/ctrl+v`
-   Quit with `ctrl+q`, instead of `ctrl+c`
-   `tab` indents selected text or inserts four-ish spaces in text editor; `shift+tab` dedents selected text
-   scroll up and down with `ctrl+up` and `ctrl+down`
-   fixes an issue where an extra space would be added to the end of lines when pressing `enter` in some situations.

## [0.0.7] - 2023-05-12

-   Comment selected text with `ctrl+/`
-   Smarter indentation after pressing `enter`

## [0.0.6] - 2023-05-09

-   Select text in the query editor using `shift` and arrow keys, etc. Replace/delete/quote selection, etc.
-   Improves behavior of inserting opening brackets in the query editor.
-   Hopefully fixes Docker build

## [0.0.5] - 2023-05-08

-   Adds column types to the column header in the results viewer.
-   Text editor now handles page up/down and ctrl + right/left keys.
-   Adds Dockerfile and Docker docs. We now publish an official Docker image to GHCR.
-   Fixes compatibility with all Pythons >= 3.8

## [0.0.4] - 2023-05-05

-   All-new text area for query editing, with syntax highlighting, scrolling, and more.
-   Loading states and progress bars for long-running queries. Better async use to maintain responsiveness.
-   Fixed edge cases around empty and repeated queries.

## [0.0.3] - 2023-05-04

-   Queries now run asynchronously.
-   Errors from DuckDB are now handled and shown in a pop-up.
-   View columns and data types in the schema viewer sidebar.
-   Queries can be formatted using ``ctrl+` ``.
-   Queries can be saved using `ctrl+s` and opened (loaded) using `ctrl+o`.

## [0.0.2] - 2023-05-02

-   View the schema of a DuckDB database in the sidebar.
-   Run queries and view the results.

## [0.0.1] - 2023-05-02

-   Use the DuckDB CLI.

[Unreleased]: https://github.com/tconbeer/harlequin/compare/0.0.8...HEAD

[0.0.8]: https://github.com/tconbeer/harlequin/compare/0.0.7...0.0.8

[0.0.7]: https://github.com/tconbeer/harlequin/compare/0.0.6...0.0.7

[0.0.6]: https://github.com/tconbeer/harlequin/compare/0.0.5...0.0.6

[0.0.5]: https://github.com/tconbeer/harlequin/compare/0.0.4...0.0.5

[0.0.4]: https://github.com/tconbeer/harlequin/compare/0.0.3...0.0.4

[0.0.3]: https://github.com/tconbeer/harlequin/compare/0.0.2...0.0.3

[0.0.2]: https://github.com/tconbeer/harlequin/compare/0.0.1...0.0.2

[0.0.1]: https://github.com/tconbeer/harlequin/compare/39e26b6dda462cd430eda69daf5ef7157dac4da6...0.0.1
