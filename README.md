# harlequin
A Terminal-based SQL IDE for DuckDB.

![harlequin TUI](harlequinv004.gif)

(A Harlequin is also a [pretty duck](https://en.wikipedia.org/wiki/Harlequin_duck).)

![harlequin duck](harlequin.jpg)

## Installing Harlequin

After installing Python 3.8 or above, install Harlequin using `pip` or `pipx` with:

```bash
pipx install harlequin
```

> **Tip:**
>
> You can run invoke directly with [`pipx run`](https://pypa.github.io/pipx/examples/#pipx-run-examples) anywhere that `pipx` is installed. For example:
> - `pipx run harlequin --help`
> - `pipx run harlequin ./my.duckdb`


## Running Harlequin in a Container

Without a database file:

```bash
docker run ghcr.io/tconbeer/harlequin:latest
```

Mounting a database file `./foo.db` into the container's working directory, `/data`:

```bash
docker run -v $(pwd)/foo.db:/data/bar.db ghcr.io/tconbeer/harlequin:latest harlequin bar.db
```

## Using Harlequin

From any shell, to open a DuckDB database file:

```bash
harlequin "path/to/duck.db"
```

To open an in-memory DuckDB session, run Harlequin with no arguments:

```bash
harlequin
```

### Viewing the Schema of your Database

When Harlequin is open, you can view the schema of your DuckDB database in the left sidebar. You can use your mouse or the arrow keys + enter to navigate the tree. The tree shows schemas, tables/views and their types, and columns and their types.

### Editing a Query

The main query editor is a full-featured text editor, with features including syntax highlighting, auto-formatting with ``ctrl + ` ``, text selection, copy/paste, and more.

You can save the query currently in the editor with `ctrl + s`. You can open a query in any text or .sql file with `ctrl + o`.

### Running a Query and Viewing Results

To run a query, press `ctrl + enter`. Up to 50k records will be loaded into the results pane below the query editor. When the focus is on the data pane, you can use your arrow keys or mouse to select different cells.

### Exiting Harlequin

Press `ctrl + q` to quit and return to your shell.