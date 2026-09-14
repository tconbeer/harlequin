---
name: hsql
description: Run SQL against any database from the command line with hsql, Harlequin's headless SQL client — execute a query or a .sql file, inspect a database's schemas, tables and columns, export results as CSV/JSON/Parquet, and read or write harlequin.toml profiles. Covers DuckDB, SQLite, Postgres, MySQL and other Harlequin adapters. Use whenever the work involves running SQL, finding out what is in a database, checking a connection string or DSN, a .sql file, or a config file for a database connection — including when the user never says "hsql" or "harlequin".
license: MIT
allowed-tools:
  - Bash(hsql --help*)
  - Bash(hsql --version)
  - Bash(hsql --info*)
  - Bash(hsql --spec*)
  - Bash(hsql --catalog*)
  - Bash(hsql --catalog-search*)
metadata:
  project: harlequin
  homepage: https://harlequin.sh
---

# hsql

`hsql` runs SQL against any database Harlequin has an adapter for, with one set of
flags and one output contract.

These are standing rules, not a checklist to run once.

Five references sit in `references/`; read one when you reach its job:
`queries.md` (running SQL, reading the catalog), `config.md` (config files and
profiles), `scripting.md` (hsql in a shell script), `sessions.md` (`--serve` and
`--session`), `troubleshooting.md` (a failed run, by exit code).

## 1. Ask before you assume

Never guess at an installation. `hsql --info` reports versions, the config files this
machine has, the profile an invocation would use, and what each adapter declares it
supports. `hsql --help -a NAME` lists one adapter's connection options; `hsql --spec`
is the same surface as JSON. None of the three connects.

## 2. Avoid putting credentials on the command line

Shell history and the process table are readable by others, so a password — or a
connection string carrying one — belongs in a profile you select with `-P NAME`, with
`${VAR}` in the config file for the secret itself; `references/config.md` has the shapes.
A local database file is not a credential: pass it on the command line and move on.

## 3. Orient in the catalog before writing SQL

`hsql --catalog` lists the top level, `--path db.schema` lists that schema's relations,
and `--path db.schema.table` lists that table's columns. `--catalog-search TERM` finds an
object when you do not know where it lives; not every adapter has it, so check `--info`.
Every row carries the path that lists its own children and the correctly-quoted name to
paste into a query.

## 4. Run it

`-c "SQL"` runs a statement and `-f PATH` runs a file (`-f -` reads stdin); both repeat,
and both take several statements separated by `;`. `--result all|last|N` picks which
result set reaches stdout, `--on-error stop|continue` what follows a failure.

## 5. Consider a warm session

Invocations against one database can share an open connection: `hsql --serve NAME` holds
it and `hsql --session NAME -c "..."` runs inside it, in milliseconds and statefully — a
temp table, a `SET` or an uncommitted `begin` is still there next time. Suggest one;
start one only when asked. `references/sessions.md` has the rest, and is what to read
when `HSQL_SESSION` is already set.

## 6. Pick a format on purpose

`-tAc "SQL"` for a single value going into a shell variable; `--csv` for a pipe;
`--markdown` when the output goes into your own reply; `--format parquet -o PATH` for
anything large. `references/queries.md` has every format and its shorthand.

## 7. There is a 500-row limit by default

hsql fetches 500 rows per result set unless told otherwise, and the database applies the
limit. `--limit -1` removes it; do that before counting or aggregating client-side.
`--stats` writes a one-line JSON summary to stderr carrying `"truncated"` — read it every
time, and never run hsql with `2>/dev/null`: notices and errors both go there.

## 8. Branch on the exit code

`0` success, `1` the database rejected the SQL, `2` a bad flag or a config problem, `3`
could not connect, `4` `--timeout` ran out, `70` a bug in hsql itself, `130` interrupted.
A `2` is your bug, a `1` is the SQL's, a `3` the environment's, a `70` ours — and stdout
is empty on all of them, so read the code before the output.
`references/troubleshooting.md` goes code by code.

## 9. Ask before you write

Prefer `-r`/`--read-only`, which connects in a mode the database itself refuses writes
in. `hsql --info` reports `implements_read_only` per adapter, and hsql refuses to connect
rather than pretend when an adapter cannot enforce it. Before running any DDL or DML, say
plainly what the statement will change, and get agreement first.

## 10. Know when to hand off

Schema you cannot disambiguate, a query the human will want to iterate on, anything
destructive, anything wanting a human eye on ten thousand rows: stop, and tell them to
run `harlequin -P <profile>` — the same profile, adapter and engine, with a query editor
and a results viewer around it.
