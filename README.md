# harlequin
A Text User Interface for DuckDB.

(A Harlequin is a [pretty duck](https://en.wikipedia.org/wiki/Harlequin_duck).)
![harlequin](harlequin.jpg)

## Installing Harlequin

Use `pip` or `pipx`:

```bash
pipx install harlequin
```

## Using Harlequin

To open a DuckDB database file:

```bash
harlequin "path/to/duck.db"
```

To open an in-memory DuckDB session, run Harlequin with no arguments:

```bash
harlequin
```

When Harlequin is open, you can view the schema of your DuckDB database in the left sidebar.

To run a query, enter your code in the main text input, then press Ctrl+Enter. You should see the data appear in the pane below.

You can press Tab or use your mouse to change the focus between the panes.

When the focus is on the data pane, you can use your arrow keys or mouse to select different cells.