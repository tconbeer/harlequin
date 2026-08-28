# Running queries and reading the catalog

Read this when you are about to run SQL, or when you need to know what is in a database.

## Finding out what is there

`--catalog` and `--catalog-search` exit without running SQL, and both are rows, so every
format and output option applies to them.

```bash
hsql -P prod --catalog                            # databases
hsql -P prod --catalog --path mydb                # that database's schemas
hsql -P prod --catalog --path mydb.analytics      # that schema's tables and views
hsql -P prod --catalog --path mydb.analytics.orders   # that table's columns and types
hsql -P prod --catalog --path 'mydb.analytics.ord*'   # a trailing * filters a listing
hsql -P prod --catalog-search customer_id         # every level at once, by name
hsql -P prod --catalog-search order --path mydb.analytics  # narrowed to a subtree
```

Each row has five columns: `path` (what lists that object's own children), `name`,
`query_name` (already quoted correctly for this database — paste this into SQL, do not
build the identifier yourself), `type`, and `type_label`.

Not every adapter implements search. Check before you rely on it:

```bash
hsql --info -a postgres    # look at .adapters.postgres.capabilities
```

An adapter that cannot search says so and exits rather than walking its whole catalog.

## Running statements

```bash
hsql -P prod -c "select * from orders limit 10"
hsql -P prod -f ./report.sql
cat report.sql | hsql -P prod -f -
hsql -P prod -f ./setup.sql -c "select count(*) from staged" -f ./build.sql
```

`-c` and `-f` both repeat, both accept several statements separated by `;`, and they run
in the order they were typed. `--result` picks what reaches stdout when there is more
than one result set:

- `--result all` (the default) emits every result set. Only the layouts and `jsonl`
  can hold more than one; `csv`, `json` and `parquet` exit `2` rather than silently
  concatenating.
- `--result last` emits only the final one — the usual choice for a setup script that
  ends in a `select`.
- `--result 2` emits the second.

`--on-error stop` (the default) stops at the first failure; `--on-error continue` runs
the rest and still exits non-zero.

## Choosing a format

`--format NAME` takes `table`, `markdown` (`md`), `vertical`, `csv`, `tsv`, `json`,
`jsonl` (`ndjson`), `parquet`, `orc`, `feather` (`arrow`), or `none`. Five have a
shorthand flag: `--csv`, `--json`, `--jsonl`, `--markdown`, `--vertical` (also `-x`).

| you want | use |
| --- | --- |
| one value for a shell variable | `-tAc "select …"` |
| a few rows you will read yourself | the default `table` |
| rows to paste into your reply | `--markdown` |
| a wide row you need to read field by field | `-x` |
| input for another program | `--csv`, or `--jsonl` |
| more rows than you want in a terminal | `--format parquet -o out.parquet` |
| the run's side effects, not its rows | `--format none` |

Layout switches are independent: `-t` drops the header and footer, `-A` drops the column
alignment, `--no-header` / `--no-footer` drop one apiece, `--null-string TEXT` sets how
NULL prints. `-tAc` is those first two plus `-c`, and prints a bare value.

## Limits, and the two kinds

`--limit N` is the hard one: the database returns at most N rows. It defaults to `500`.
`--limit -1` is unlimited, and is what you want before any aggregate you compute
yourself.

`--display-rows N` is soft: hsql fetched the rows and prints N of them. It only applies
to the text layouts (`table` and `md` print 40, `vertical` prints 10) and never changes
a file format's contents.

`--stats` reports both, on stderr, as one line of JSON:

```bash
$ hsql -c "select 1" --stats --format none
{"status":"ok","statements":1,"rows":1,"truncated":false,"limit":500,"elapsed_ms":1,"columns":[{"name":"1","type":"#"}]}
```

`"truncated": true` means the limit cut the result short and the numbers you are about
to report are wrong. Re-run with `--limit -1`, or aggregate in SQL instead.

## Writing to a file

`-o PATH` writes to a file; `-o DIR/` writes one file per result set, named for you, and
says on stderr what it called them. The bytes are identical to a shell redirect, so
`-o out.csv` and `> out.csv` agree.

```bash
hsql -P prod --limit -1 -c "select * from users" --format parquet -o users.parquet
hsql -P prod --limit -1 -f ./three_reports.sql --csv -o ./out/
```

stdout carries data and nothing else. Every note, warning and error goes to stderr.
