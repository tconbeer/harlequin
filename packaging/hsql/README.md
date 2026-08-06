# hsql

`hsql` is the headless command-line interface to
[Harlequin](https://harlequin.sh), the SQL IDE for your terminal — the same
engine and the same database adapters, without the TUI, for scripts, CI, and
coding agents.

## Status

**This package is a placeholder that installs Harlequin.** The `hsql` command
itself is not here yet; it will ship as part of the `harlequin` distribution,
and this package will then require the version that provides it.

Installing it today gets you Harlequin:

```bash
pip install hsql
harlequin --help
```

If that is what you want, `pip install harlequin` is the more direct way to
say so.

## What it will be

One CLI contract across every database Harlequin supports — DuckDB, SQLite,
Postgres, MySQL, BigQuery, Trino, Databricks, ODBC, ADBC and more — reusing
the profiles you already have in `.harlequin.toml`:

```bash
hsql -P prod -c "select count(*) from orders"
hsql -P prod catalog analytics
hsql -P prod -tAc "select count(*) from orders"
```

Same flags, same output formats, same exit codes, whichever database is on the
other end.

## Links

- Documentation: <https://harlequin.sh>
- Source: <https://github.com/tconbeer/harlequin>
- Issues: <https://github.com/tconbeer/harlequin/issues>

MIT licensed.
