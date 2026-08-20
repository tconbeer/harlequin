# hsql

`hsql` is your agent's favorite SQL client. It's the headless CLI for
[Harlequin](https://harlequin.sh), and shares the same config and query engine,
with an interface optimized for agents, scripts, and automations.

> [!TIP]
> This README contains a small subset of the docs available at
> [harlequin.sh](https://harlequin.sh/docs/getting-started/hsql).

If you already use Harlequin and know about adapters and config files, jump ahead to [Running hsql](#running-hsql).

## Installing hsql

hsql is packaged with [Harlequin](https://pypi.org/project/harlequin/), so if you already use Harlequin,
hsql is already installed.

Otherwise, you can install hsql directly. hsql is a Python program, and there are many ways to install and run it. We strongly recommend using [uv](https://docs.astral.sh/uv):

1. [Install uv](https://docs.astral.sh/uv/getting-started/installation/#standalone-installer). From a POSIX shell, run:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Or using Windows Powershell:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. Install hsql as a tool using `uv`:

   ```bash
   uv tool install hsql
   ```

   This command will install hsql into an isolated environment and add it to your PATH so you can easily run the executable.

## Installing Database Adapters

hsql can connect to dozens of databases using adapter plug-ins. Adapters are distributed as their own Python packages that need to be installed into the same environment as hsql and harlequin.

For a list of known adapters provided either by the Harlequin maintainers or the broader community, see the [adapters](https://harlequin.sh/docs/adapters) page.

The adapter docs also include installation instructions (installing an adapter with Harlequin also installs it for hsql). Some adapters can be installed as Harlequin extras, like `postgres`. If you used `uv` to install hsql directly, you can add adapter packages using `--with`:

```bash
uv tool install hsql --with harlequin-postgres
```

You can install multiple extras:

```bash
uv tool install hsql --with harlequin-postgres --with harlequin-mysql
```

## Running hsql

Once hsql is installed, you run it from the command line. If you have used psql or the duckdb CLI, hsql will feel familiar, but hsql has the major advantage that is works with most databases and provides the same interface and produces the same output, regardless of the connected database. This means you (and your agent) can learn one tool, instead of several. In your shell, all hsql commands take the same form:

```bash
hsql [OPTIONS] [CONN_STR]
```

where `[OPTIONS]` is 0 or more pairs of the form `--[option-name] [option-value]`, and `[CONN_STR]` is 0 or more connection strings. `[OPTIONS]` are composed of both hsql options and adapter options. For a full list of options, run hsql with the `--help` option:

```bash
hsql --help
```

## Using hsql with DuckDB

hsql defaults to using its DuckDB database adapter, which ships with hsql and includes the full DuckDB in-process database.

Run a query against an in-memory DuckDB session, run hsql and pass in a query with the `-c` option:

```bash
$ hsql -c "select 1"
 1
---
 1
(1 row)
```

To query one or more DuckDB database files, pass in relative or absolute paths as connection strings (hsql will create DuckDB databases if they do not exist):

```bash
$ hsql "path/to/duck.db" -tAc "select count(*) from orders"
42
```

## Using hsql with SQLite and Other Adapters

hsql also ships with a SQLite3 adapter. To use that adapter, you specify the `--adapter sqlite` option. Like DuckDB, you can open an in-memory SQLite database by omitting the connection string:

```bash
$ hsql --adapter sqlite -c "select 'Ted' as author"
 author
--------
 Ted
(1 row)
```

You can query one or more SQLite database files by passing in their paths as connection strings; note that the `--adapter` option has a short alias, `-a`:

```bash
$ hsql -a sqlite "path/to/sqlite.db" -c "select * from users"
 id | name
----+---------
 1  | Ted
 2  | Patrick
(2 rows)
```

Other adapters work the same way; for example, Postgres:

```bash
$ hsql -a postgres "postgresql://example.com/postgres:5432" -c "select * from invoices"
```

> [!TIP]
> You should use Profiles to keep credentials out of your shell
> history. For more information, keep reading or see
> [the docs](https://harlequin.sh/docs/config-file/index) on
> config files.

## Configuring hsql and Using Profiles

hsql supports a number of options for setting the query limit, configuring output formats, and defining connection parameters. Options can be passed as command-line flags, or read from [config files](https://harlequin.sh/docs/config-file/index). Config files store configurations under separate profiles, so you can easily switch between databases by reading from different profiles with the `-P` option:

```bash
$ hsql -P prod -c "select count(*) from orders" --csv
$ hsql -P dev -c "select * from users" --vertical --limit 5
$ hsql -P warehouse -c "..." --parquet -o invoices.pq
```

hsql reads the same config files as Harlequin, in the same order, nearest first:

1. `harlequin.toml`, `.harlequin.toml`, or the `[tool.harlequin]` table of `pyproject.toml`, in the current working directory
2. `harlequin.toml`, `.harlequin.toml`, or `config.toml`, in your user config directory (like `~/.config/harlequin`)
3. `harlequin.toml`, `.harlequin.toml`, or `pyproject.toml`, in your home directory

Files merge one profile at a time. If two files define a profile with the same name, the nearer file's version of that profile wins whole; profiles that only a farther file defines are still available to `-P`. Pass `--config-path PATH` to read one file instead of the discovered ones, or `-P None` to skip config files entirely and use hsql's own defaults.

### Inspecting Your Config

Four `--config` modes report on your config files instead of running SQL. None of them connects to a database.

`--config list-profiles` answers "what can I pass to `-P`?":

```bash
$ hsql --config list-profiles
 profile | adapter | default
---------+---------+---------
 dev     | sqlite  | true
 prod    | duckdb  | false
(2 rows)
```

`--config show` answers "which file is winning?" It prints the merged config as TOML, with the file each value came from written beside it, and the files it overrode after that:

```bash
$ hsql --config show
default_profile = "dev" # from /home/user/.config/harlequin/harlequin.toml

[profiles.dev] # from /home/user/proj/harlequin.toml, overriding /home/user/.config/harlequin/harlequin.toml
adapter = "sqlite"
conn_str = ["./dev.db"]
limit = 100

[profiles.prod] # from /home/user/proj/harlequin.toml
adapter = "duckdb"
conn_str = ["./prod.duckdb"]
format = "csv"
limit = -1
```

Pass `--json` for the same answer as JSON, where every key carries its `value`, the file it came `from`, and the list of files it `overrode`.

`--config validate` answers "what is wrong with any of it?" It reports every problem in every discovered file, rather than stopping at the first one a run would have hit, and exits `2` if it finds any:

```bash
$ hsql --config validate
 file                           | key                 | problem                                                                                                 | line
--------------------------------+---------------------+---------------------------------------------------------------------------------------------------------+------
 /home/user/proj/harlequin.toml | profiles.dev.limitt | Profile defines an option 'limitt', which is not an option of the sqlite adapter. Did you mean 'limit'? | NULL
 /home/user/proj/harlequin.toml | default_profile     | Config files set the default_profile to dve, but no config file defines a profile with that name.       | NULL
(2 rows)
```

`list-profiles` and `validate` are result sets, so every layout and file option works on them: `--csv`, `--json`, `-t`, `-A` and `-o` all do what they do for a query.

### Writing a Profile

`--config init` writes a profile into a config file from the options you pass, and prompts for nothing:

```bash
$ hsql --config init -P prod -a sqlite ./my.db --limit -1 --csv
note: wrote the profile named prod to /home/user/proj/.harlequin.toml.
```

which writes:

```toml
[profiles.prod]
adapter = "sqlite"
conn_str = ["./my.db"]
limit = -1
format = "csv"
```

It writes only the options you typed, and it writes the profile whole: a key you do not pass is a key that profile no longer has. Without `--config-path`, it edits the nearest config file that already exists, or creates a `.harlequin.toml` in the working directory when there is none; either way, the comments and formatting elsewhere in the file survive. If you would rather answer questions than pass flags, `harlequin --config` is the interactive version of the same thing.

### Editor Completion and Validation

`--config schema` writes a [JSON Schema](https://json-schema.org/) for a Harlequin config file, built from this installation, so it covers the options of every adapter you have installed:

```bash
$ hsql --config schema -o harlequin-schema.json
```

Point your editor's TOML language server at that file to get completion and validation as you type in your `harlequin.toml`.

## Data Layouts and File Formats

hsql supports all of the following formats for displaying and writing data:
- table
- markdown (alias: md)
- vertical
- csv
- tsv
- json
- jsonl (alias: ndjson)
- parquet
- orc
- feather (alias: arrow)
- none (suppresses output)

You can select a format with the `--format <name>` or using the shorthand `--<name>`, so these are equivalent: `--format csv`, `--csv`.

Some layouts can present the results from multiple queries. Others will raise an error and exit with code 2 if multiple queries are executed.

Additionally, for any layout, pass `--stats` to print summary info as JSON to stderr:

```bash
$ hsql -c "select 1" --format none  --stats
{"status":"ok","statements":1,"rows":1,"truncated":false,"limit":500,"elapsed_ms":1,"columns":[{"name":"1","type":"#"}]}
```

## Scripting with hsql

> [!WARNING]
> To make hsql safe and efficient for agents, by default hsql applies a 500-row
> limit to all queries. To remove this limit, use `--limit -1` or set
> `limit = -1` in your profile. If limits truncate data, hsql will print
> a warning on stderr; we recommend that you do NOT suppress or redirect 
> that message so do NOT use hsql with `2>/dev/null`.

hsql can write data to files, either with the `-o` option or by piping output (hsql only writes data to stdout; other messages go to stderr):

```bash
$ hsql -P prod --limit -1 -c "select * from users" --parquet -o "users.pq"
$ hsql -P prod --limit -1 -c "select * from users" --csv > users.csv
```

hsql can execute multiple statements in one invocation, and supports several methods for doing so:
- Pass `-c` multiple times
- Include multiple queries, separated by `;`, in one `-c` option
- Pass one or more .sql files with `-f`, with multiple statements in each
- Use `--results` to define which queries output data to stdout
- Use `--on-error` to either `stop` or `continue` if one or more queries produces an error.

In other words, this works:
```bash
$ hsql -P prod --limit -1 --format md --results all --on-error stop \
    -f ./setup.sql \
    -c "select count(*) from raw_table" \
    -f ./build-models.sql \
    -c "select count(*) from modeled_table" 
```

hsql's exit codes are meaningful and stable:

- 0: Success
- 1: Query error
- 2: Usage/config error
- 3: Connection error
- 4: Timeout
- 130: Interrupted

You can also use `--stats` and `jq` together to error on a truncated query:
```bash
hsql --limit -1 -c "select 1" --csv -o data.csv --stats 2>&1 | jq -e '.truncated | not' > /dev/null
```

## Describing hsql to an Agent

`hsql --help` is written for a person. Two flags answer the same questions as JSON, so an agent can find its way around an installation it has never seen. Neither one connects to a database, and both write JSON to stdout whatever `--format` says, so `-o` and a pipe into `jq` both work.

`--spec` is a machine-readable `--help`: every option on the command, plus the connection options every installed adapter declares. Each one carries its declarations, type, choices, default, and help text:

```bash
$ hsql --spec -a sqlite | jq '.adapters.sqlite.options[] | select(.name == "read_only")'
{
  "name": "read_only",
  "decls": [
    "--read-only",
    "-readonly",
    "-r"
  ],
  "type": "boolean",
  "metavar": null,
  "choices": null,
  "default": false,
  "multiple": false,
  "is_flag": true,
  "required": false,
  "envvar": null,
  "help": "Open the database file in read-only mode."
}
```

Alongside `adapters`, the document holds `program`, `version`, `scope`, the `arguments` the command takes, and hsql's own `options` in the same shape. Without `-a`, `adapters` holds every installed adapter, which means importing all of them; `-a NAME` narrows it to one and is much faster. `--spec` covers hsql's own surface only — the `harlequin` IDE's flags are not in it, and the `scope` field in the output says so.

`--info` describes the installation rather than the command: versions, platform, the config files hsql discovered in precedence order, the profile that would be active, and what each installed adapter declares it supports.

```bash
$ hsql --info -a sqlite -P prod
{
  "program": "hsql",
  "version": "2.9.0",
  "python": {
    "version": "3.10.15",
    "implementation": "CPython",
    "executable": "/home/user/.local/share/uv/tools/hsql/bin/python"
  },
  "platform": {
    "system": "Linux",
    "release": "6.8.0-generic",
    "machine": "x86_64"
  },
  "config": {
    "path": null,
    "files": [
      "/home/user/proj/harlequin.toml",
      "/home/user/.config/harlequin/harlequin.toml"
    ]
  },
  "profile": {
    "name": "prod",
    "options": {
      "adapter": "sqlite",
      "conn_str": [
        "./my.db"
      ],
      "limit": -1,
      "format": "csv"
    },
    "error": null
  },
  "adapter": {
    "name": "sqlite",
    "from": "-a"
  },
  "adapters": {
    "sqlite": {
      "distribution": "harlequin",
      "version": "2.9.0",
      "capabilities": {
        "implements_cancel": true
      },
      "error": null
    }
  }
}
```

Use it to check whether an adapter is installed, and what it can do, before writing a script that depends on the answer. As with `--spec`, `-a NAME` narrows `adapters` to one. An adapter that is installed but cannot be imported is reported with its `error` rather than left out.

## Keep Reading at [harlequin.sh](https://harlequin.sh/docs/getting-started/hsql)

Visit [harlequin.sh](https://harlequin.sh/docs/getting-started/hsql) for an overview of features and full documentation.

## Getting Help

To view all command-line options for Harlequin and all installed adapters, after installation, simply type:

```bash
hsql --help
```

[GitHub Discussions](https://github.com/tconbeer/harlequin/discussions) are a good place to ask questions, request features, and say hello.

[GitHub Issues](https://github.com/tconbeer/harlequin/issues) are the best place to report bugs.

## Sponsoring Harlequin and hsql

Please consider [sponsoring Harlequin's author](https://github.com/sponsors/tconbeer), so he can continue to dedicate time to hsql.

## Contributing

Thanks for your interest in Harlequin! Harlequin and hsql are primarily maintained by [Ted Conbeer](https://github.com/tconbeer), but he welcomes all contributions!

Please see [`CONTRIBUTING.md`](./CONTRIBUTING.md) for more information.
