# hsql

`hsql` is the headless command-line interface to
[Harlequin](https://harlequin.sh), the SQL IDE for your terminal — the same
engine and the same database adapters, without the TUI, for scripts, CI, and
coding agents.

## Install

```bash
uvx hsql --help
```

or, to keep it around:

```bash
uv tool install hsql
# or
pip install hsql
```

This package is a metapackage: the `hsql` command itself ships in the
`harlequin` distribution, which this installs. `pip install harlequin` gets you
the same two commands — `harlequin` and `hsql` — by the other name. The two are
released together and share a version number, so `hsql 2.9.0` is exactly
`harlequin 2.9.0`.

## What it does

One CLI contract across every database Harlequin supports — DuckDB, SQLite,
Postgres, MySQL, BigQuery, Trino, Databricks, ODBC, ADBC and more — reusing
the profiles you already have in `.harlequin.toml`, so a script or an agent
never handles a credential:

```bash
hsql -P prod -c "select count(*) from orders"
hsql -P prod -c "select * from orders" --csv -o orders.csv
hsql -P prod -tAc "select count(*) from orders"
```

Same flags, same output formats, same exit codes, whichever database is on the
other end. stdout is data and stderr is narration, so redirecting stdout gives
you a clean file; exit codes are an API (0 success, 1 query error, 2 usage or
config error, 3 connection error, 130 interrupted).

Adapters install alongside it:

```bash
uvx --with harlequin-postgres hsql -P prod -c "select 1"
```

## Links

- Documentation: <https://harlequin.sh>
- Source: <https://github.com/tconbeer/harlequin>
- Issues: <https://github.com/tconbeer/harlequin/issues>

MIT licensed.
