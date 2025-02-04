# Harlequin

[![PyPI](https://img.shields.io/pypi/v/harlequin)](https://pypi.org/project/harlequin/)

![PyPI - Python Version](https://img.shields.io/pypi/pyversions/harlequin)
![Runs on Linux | MacOS | Windows](https://img.shields.io/badge/runs%20on-Linux%20%7C%20MacOS%20%7C%20Windows-blue)

The SQL IDE for Your Terminal.

![Harlequin](./harlequin.svg)

## Installing Harlequin

Harlequin is a Python program, and there are many ways to install and run it. We strongly recommend using [uv](https://docs.astral.sh/uv):

1. [Install uv](https://docs.astral.sh/uv/getting-started/installation/#standalone-installer). From a POSIX shell, run:

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```
    Or using Windows Powershell:

    ```powershell
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```
2. Install Harlequin as a tool (in an isolated environment) using `uv`:

    ```bash
    uv tool install harlequin
    ```

Alternatively, if you know what you're doing, after installing Python 3.9 or above, install Harlequin using `pip`, `pipx`, `poetry`, or any other program that can install Python packages from PyPI:

```bash
pip install harlequin
```

## Installing Database Adapters

Harlequin can connect to dozens of databases using adapter plug-ins. Adapters are distributed as their own Python packages that need to be installed into the same environment as Harlequin.

For a list of known adapters provided either by the Harlequin maintainers or the broader community, see the [adapters](https://harlequin.sh/docs/adapters) page in the docs.

The adapter docs also include installation instructions. Some adapters can be installed as Harlequin extras, like `postgres`. If you used `uv` to install Harlequin:

```bash
uv tool install harlequin[postgres]
```

You can install multiple extras:

```bash
uv tool install harlequin[postgres,mysql,s3]
```

Some adapters are not available as extras, and have to be installed manually. You may also wish to do this to control the version of the adapter that Harlequin uses. You can add adapters to your installation using uv's `--with` option:

```bash
uv tool install harlequin --with harlequin-odbc
```

## Using Harlequin with DuckDB

Harlequin ships with a DuckDB adapter, so no additional installation is required.

From any shell, to open one or more DuckDB database files:

```bash
harlequin "path/to/duck.db" "another_duck.db"
```

To open an in-memory DuckDB session, run Harlequin with no arguments:

```bash
harlequin
```

If you want to control the version of DuckDB that Harlequin uses, see the [Troubleshooting](https://harlequin.sh/docs/troubleshooting/duckdb-version-mismatch) page.

## Using Harlequin with SQLite and Other Databases

Harlequin also ships with a SQLite3 adapter. You can open one or more SQLite database files with:

```bash
harlequin --adapter sqlite "path/to/sqlite.db" "another_sqlite.db"
```

Like DuckDB, you can also open an in-memory database by omitting the paths; the `--adapter` option also has a shorter alias, `-a`:

```bash
harlequin -a sqlite
```

You can follow the same pattern to connect to a database using a different adapter. Simply pass the adapter name as an option, followed by a connection string and any options. For example, a local Postgres database:

```bash
harlequin -a postgres "postgresql://localhost:5432/postgres" -u myuser
```

Many database adapters will read from the standard environment variables for connection parameters. For example, the Postgres adapter will read and use the `PGHOST`, `PGUSER`, and `PGPASSWORD` variables (and all the others).

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
