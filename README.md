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

If you want to control the version of DuckDB that Harlequin uses, see the [Troubleshooting](troubleshooting/duckdb-version-mismatch) page.

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

## More info at [harlequin.sh](https://harlequin.sh)

Visit [harlequin.sh](https://harlequin.sh) for an overview of features and full documentation.

## Contributing

Thanks for your interest in Harlequin! Harlequin is primarily maintained by [Ted Conbeer](https://github.com/tconbeer), but he welcomes all contributions and is looking for additional maintainers!

### Providing Feedback

We'd love to hear from you! [Open an Issue](https://github.com/tconbeer/harlequin/issues/new) to request new features, report bugs, or say hello.

### Setting up Your Dev Environment and Running Tests

1. Install Poetry v1.2 or higher if you don't have it already. You may also need or want pyenv, make, and gcc.
1. Fork this repo, and then clone the fork into a directory (let's call it `harlequin`), then `cd harlequin`.
1. Use `poetry install --sync` to install the project (editable) and its dependencies (including all test and dev dependencies) into a new virtual env.
1. Use `poetry shell` to spawn a subshell.
1. Type `make` to run all tests and linters, or run `pytest`, `black .`, `ruff . --fix`, and `mypy` individually.

### Opening PRs

1. PRs should be motivated by an open issue. If there isn't already an issue describing the feature or bug, [open one](https://github.com/tconbeer/harlequin/issues/new). Do this before you write code, so you don't waste time on something that won't get merged.
2. Ideally new features and bug fixes would be tested, to prevent future regressions. Textual provides a test harness that we use to test features of Harlequin. You can find some examples in the `tests` directory of this project. Please include a test in your PR, but if you can't figure it out, open a PR to ask for help.
2. Open a PR from your fork to the `main` branch of `tconbeer/harlequin`. In the PR description, link to the open issue, and then write a few sentences about **why** you wrote the code you did: explain your design, etc.
3. Ted may ask you to make changes, or he may make them for you. Don't take this the wrong way -- he values your contributions, but he knows this isn't your job, either, so if it's faster for him, he may push a commit to your branch or create a new branch from your commits.
