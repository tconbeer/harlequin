### Using Harlequin with DuckDB

Harlequin defaults to using its DuckDB database adapter.

From any shell, to open one or more DuckDB database files:

```bash
harlequin "path/to/duck.db" "another_duck.db"
```

To open an in-memory DuckDB session, run Harlequin with no arguments:

```bash
harlequin
```

If you want to control the version of DuckDB that Harlequin uses, see the [Troubleshooting](https://harlequin.sh/docs/troubleshooting/duckdb-version-mismatch) page.

### Using Harlequin with SQLite and Other Adapters

Harlequin also ships with a SQLite3 adapter. You can open one or more SQLite database files with:

```bash
harlequin -a sqlite "path/to/sqlite.db" "another_sqlite.db"
```

Like DuckDB, you can also open an in-memory database by omitting the paths:

```bash
harlequin -a sqlite
```

Other adapters must be installed into the same environment as Harlequin; instructions will depend on how you installed Harlequin. For a list of known adapters provided either by the Harlequin maintainers or the broader community, see the [adapters](https://harlequin.sh/docs/adapters) page.


### Getting Help

To view all command-line options for Harlequin and all installed adapters, after installation, simply type:

```bash
harlequin --help
```

See the [Troubleshooting](https://harlequin.sh/docs/troubleshooting/index) guide for help with key bindings, appearance issues, copy-paste, etc.

[GitHub Issues](https://github.com/tconbeer/harlequin/issues) are the best place to report bugs.

[GitHub Discussions](https://github.com/tconbeer/harlequin/discussions) are a good place to start with other issues, feature requests, etc.

### Viewing Files

Harlequin's Data Catalog will show local files in a second tab in the Data Catalog if Harlequin is initialized with the `--show-files` option (alias `-f`). `--show-files` takes an absolute or relative file path to a directory as its argument:

For example, an absolute path:

```bash
harlequin --show-files /path/to/my/data
```

For the current working directory:

```bash
harlequin -f .
```

Harlequin can also show remote objects in S3 or a similar service. For more information, see https://harlequin.sh/docs/files/remote

### Using Config Files

Any command-line options for Harlequin can be loaded as a profile from TOML config files. For more information, see https://harlequin.sh/docs/config-file

### Changing Key Bindings

Harlequin can load sets of key bindings, called keymaps, either from plug-ins or from TOML config files. This allows you to extend or replace Harlequin's default key bindings. For more information, see https://harlequin.sh/docs/keymaps

### Managing Transactions

Different adapters handle transactions differently; many choose to auto-commit each executed query. However, some adapters define multiple Transaction Modes that allow you to fine-tune the transaction handling of the commands you run in Harlequin. For more information, see https://harlequin.sh/docs/transactions
