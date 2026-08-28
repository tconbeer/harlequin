# When a run fails

Read this when hsql exited non-zero, printed nothing, or printed something you did not
expect. Work the exit code first: stdout is empty on every failure, so the code and
stderr are the whole of the evidence.

Errors are one line, prefixed `hsql: error:`. Notes are prefixed `note:`. Both are on
stderr; if you cannot see them, something in the pipeline is discarding that stream.

## `2` — usage or config: your bug

The most common code, and the one that never means the database is unhappy. hsql never
opened a connection.

| stderr says | what happened |
| --- | --- |
| `No such option` | a flag hsql does not have. `hsql --spec` lists every real spelling, including each installed adapter's. |
| `... does not run SQL; drop -c/--command and -f/--file` | a mode (`--catalog`, `--info`, `--spec`, `--config`) was passed beside a query. Run them as two invocations. |
| `--path must be used with --catalog or --catalog-search` | `--path` only means something to those two modes. |
| `... are two modes; pass one of them` | two mode flags in one invocation. |
| a profile name, and "not defined" | `-P` named a profile no file defines. `hsql --config list-profiles` lists the real names. |
| an environment variable name | a `${VAR}` in the config file that is unset. Export it, or give it a `${VAR:-default}`. |
| a config file path and a parse error | that TOML does not parse. `hsql --config validate` names the file and the key. |
| `... cannot be honored` | `--read-only` or `--timeout` was asked of an adapter that does not implement it. `hsql --info -a NAME` reports which. |
| a format complaint about several result sets | `csv`, `json` and `parquet` hold one result set. Use `--result last`, or `--jsonl`. |

`-t` is *tuples only*, as in psql, and not the IDE's theme flag. `hsql -t nord -c "..."`
parses, and `nord` becomes a connection string; hsql says so on stderr.

## `3` — could not connect

hsql got as far as the adapter and no further. The message is the driver's, with any
password in it masked.

1. `hsql --info -a NAME` — is the adapter even installed, and did it import?
2. `hsql --config show` — what host, port and user is the profile actually supplying?
   Values are redacted, but you can see whether a `${VAR}` resolved to something.
3. `hsql -P NAME --catalog` — the cheapest end-to-end check once you have changed
   something.

An adapter that is installed but will not import is reported by `--info` with
`"capabilities": "unknown"` and the import error beside it. That is a broken
installation, not a broken config.

## `1` — the database rejected the SQL

The message is the database's own, verbatim. hsql connected fine.

- A missing relation or column: you guessed at a name. `hsql --catalog-search NAME` finds
  where it really lives, and every catalog row carries a `query_name` already quoted
  correctly for this database. Do not build identifiers by hand.
- A syntax error: the SQL dialect is the database's, not hsql's. hsql normalizes flags
  and output, never SQL.
- A permission error under `--read-only`: expected, and working as intended.

With `--on-error continue`, later statements still ran, and the exit code is still `1`.

## `4` — the timeout ran out

`--timeout` cancelled the run. hsql attributes it explicitly, because a cancelled cursor
comes back empty and error-free — exactly like a query that matched nothing — so an
empty result is never silently reported as success.

Either the query is slower than the bound, or the bound is too tight. Do not simply raise
it: check the query with `explain`, add the filter you forgot, or aggregate in SQL rather
than fetching rows to count them.

## `130` — interrupted

Someone or something sent an interrupt. Nothing to fix.

## `0`, but the output is wrong

- **Fewer rows than the table has.** The 500-row default. Run with `--stats` and look at
  `"truncated"`; `--limit -1` removes the limit.
- **All the rows, but not all printed.** `--display-rows` caps what the text layouts
  print (40 for `table` and `md`, 10 for `vertical`) without changing what was fetched.
  File formats ignore it.
- **Nothing at all on stdout.** `--format none` writes nothing by design, and `-o` sends
  the output to a file. Check both before assuming the query matched nothing.
- **A column of `NULL` you expected empty, or the reverse.** `--null-string TEXT` sets
  it; text formats default to `NULL` and csv to empty.
- **Misaligned columns.** Widths are measured in terminal cells, so CJK text and emoji
  are counted correctly — if it still looks wrong, the terminal font is the suspect, not
  hsql.

## Nothing here fits

```bash
hsql --info > info.json
```

`--info` connects to nothing, redacts the profile it reports, and answers even when the
config is broken — so it is safe to paste into a bug report. Issues go to
<https://github.com/tconbeer/harlequin/issues>.
