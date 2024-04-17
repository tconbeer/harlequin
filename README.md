# Harlequin

[![PyPI](https://img.shields.io/pypi/v/harlequin)](https://pypi.org/project/harlequin/)

![PyPI - Python Version](https://img.shields.io/pypi/pyversions/harlequin)
![Runs on Linux | MacOS | Windows](https://img.shields.io/badge/runs%20on-Linux%20%7C%20MacOS%20%7C%20Windows-blue)

The SQL IDE for Your Terminal.

![Harlequin](./harlequin.svg)

## Installing Harlequin

After installing Python 3.8 or above, install Harlequin using `pip` or `pipx` with:

```bash
pipx install harlequin
```

## Using Harlequin with DuckDB

From any shell, to open one or more DuckDB database files:

```bash
harlequin "path/to/duck.db" "another_duck.db"
```

To open an in-memory DuckDB session, run Harlequin with no arguments:

```bash
harlequin
```

If you want to control the version of DuckDB that Harlequin uses, see the [Troubleshooting](https://harlequin.sh/docs/troubleshooting/duckdb-version-mismatch) page.

## Using Harlequin with SQLite and Other Adapters

Harlequin also ships with a SQLite3 adapter. You can open one or more SQLite database files with:

```bash
harlequin -a sqlite "path/to/sqlite.db" "another_sqlite.db"
```

Like DuckDB, you can also open an in-memory database by omitting the paths:

```bash
harlequin -a sqlite
```

Other adapters can be installed using `pip install <adapter package>` or `pipx inject harlequin <adapter package>`, depending on how you installed Harlequin. For a list of known adapters provided either by the Harlequin maintainers or the broader community, see the [adapters](https://harlequin.sh/docs/adapters) page in the docs.

## Getting Help

To view all command-line options for Harlequin and all installed adapters, after installation, simply type:

```bash
harlequin --help
```

To view a list of all key bindings (keyboard shortcuts) within the app, press <Key>F1</Key>. You can also view this list outside the app [in the docs](https://harlequin.sh/docs/bindings).

COLOR, KEY BINDING, OR COPY-PASTE PROBLEMS? See [Troubleshooting](https://harlequin.sh/docs/troubleshooting/index) in the docs. 

## More info at [harlequin.sh](https://harlequin.sh)

Visit [harlequin.sh](https://harlequin.sh) for an overview of features and full documentation.

## Sponsoring Harlequin

Please consider [sponsoring Harlequin's author](https://github.com/sponsors/tconbeer), so he can continue to dedicate time to Harlequin.

## Contributing

Thanks for your interest in Harlequin! Harlequin is primarily maintained by [Ted Conbeer](https://github.com/tconbeer), but he welcomes all contributions!

Please see [`CONTRIBUTING.md`](./CONTRIBUTING.md) for more information.
