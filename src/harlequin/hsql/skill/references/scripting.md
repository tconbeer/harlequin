# hsql inside a script

Read this when hsql is going into a shell script, a Makefile, a CI job or a pipeline —
anywhere the output is consumed by a program rather than read by you.

## The shape of a safe script

```bash
#!/usr/bin/env bash
set -euo pipefail

hsql -P prod --read-only --timeout 60 --limit -1 \
    -c "select count(*) from orders" -tAc
```

Four habits, in order of how often they matter:

1. `set -euo pipefail`, so a non-zero exit from hsql actually stops the script.
2. `-P prod` rather than a connection string, so no credential is in the file, the shell
   history or the process table.
3. `--read-only` unless the script is meant to write.
4. `--limit -1` whenever the script computes something from the rows, or writes them
   anywhere. The default 500 gives a wrong answer, or a short file, and as far as the
   script can tell it succeeded.

Never write `2>/dev/null`. hsql puts truncation notices, warnings and errors on stderr
and *only* data on stdout, so suppressing stderr is exactly the thing that turns a
detected problem into a wrong number. Redirect it to a log if it is noisy — and then
have something read the log; a log nobody reads is `2>/dev/null` with extra steps.

## One value into a variable

```bash
row_count=$(hsql -P prod -tAc "select count(*) from orders")
```

`-t` drops the header and footer, `-A` drops the alignment padding, so what comes back is
the bare value with a trailing newline. This is the idiom; do not parse the `table`
layout.

## Rows into another program

```bash
hsql -P prod --limit -1 --csv -c "select * from users" | your-loader
hsql -P prod --limit -1 --jsonl -c "select * from events" | jq -r '.id'
```

`jsonl` is the format to reach for when several result sets have to travel down one pipe;
`csv`, `json` and `parquet` hold exactly one and exit `2` rather than concatenating.

## Files, and one file per query

```bash
hsql -P prod --limit -1 --format parquet -o ./out/users.parquet -c "select * from users"
hsql -P prod --limit -1 --csv -o ./out/ -f ./three_reports.sql
```

A directory `-o` writes one file per result set and names them itself, reporting the
names on stderr. `-o PATH` and `> PATH` produce identical bytes, so pick whichever reads
better.

## Several statements in one invocation

```bash
hsql -P prod --limit -1 --format md --result last --on-error stop \
    -f ./setup.sql \
    -c "select count(*) from raw_table" \
    -f ./build_models.sql \
    -c "select count(*) from modeled_table"
```

One connection, one transaction context, in the order typed. `--result last` keeps stdout
to the final answer while the earlier statements still run. For a single transaction,
write `begin` and `commit` into the script yourself — hsql has no `-1`.

## Checking the outcome

Branch on the exit code first; it is the reliable signal.

```bash
if ! hsql -P prod --read-only -c "select 1" --format none; then
    echo "database is not reachable" >&2
    exit 1
fi
```

For truncation, `--stats` and `jq` together:

```bash
hsql -P prod --limit -1 --csv -o data.csv --stats \
    -c "select * from big_table" 2>&1 \
    | jq -e '.truncated | not' > /dev/null
```

`--stats` writes one line of JSON to stderr with `status`, `statements`, `rows`,
`truncated`, `limit`, `elapsed_ms` and `columns`. It is the machine-readable summary of
a run; the exit code is the verdict.

## Bounding a run

`--timeout SECONDS` cancels the run and exits `4`. It covers executing *and* fetching,
and hsql refuses to start if the adapter cannot cancel a query, so it is a real bound
rather than a hope. In a scheduled job, set it: an unbounded query holds a connection
until something else kills it.

Both `--read-only` and `--timeout` are also profile keys, so a profile meant for
automation can carry them and no invocation has to remember:

```toml
[profiles.agent]
adapter = "postgres"
read_only = true
timeout = 30
limit = -1
```

## Portability

The same script runs against another database by changing the profile. hsql's own flags,
output layouts and exit codes do not vary by adapter; what varies is the SQL and the
connection options. `hsql --info` is how a script checks that an adapter is installed and
can do what the script needs before depending on it.
