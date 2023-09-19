# Harlequin

[![PyPI](https://img.shields.io/pypi/v/harlequin)](https://pypi.org/project/harlequin/)
[![Test](https://github.com/tconbeer/harlequin/actions/workflows/test.yml/badge.svg?branch=main&event=push)](https://github.com/tconbeer/harlequin/actions/workflows/test.yml)

![PyPI - Python Version](https://img.shields.io/pypi/pyversions/harlequin)
![Runs on Linux | MacOS | Windows](https://img.shields.io/badge/runs%20on-Linux%20%7C%20MacOS%20%7C%20Windows-blue)

The DuckDB IDE for Your Terminal.

![Harlequin Demo Video](static/harlequin.gif)


## Installing Harlequin

After installing Python 3.8 or above, install Harlequin using `pip` or `pipx` with:

```bash
pipx install harlequin
```

## Using Harlequin

From any shell, to open one or more DuckDB database files:

```bash
harlequin "path/to/duck.db" "another_duck.db"
```

To open an in-memory DuckDB session, run Harlequin with no arguments:

```bash
harlequin
```

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
