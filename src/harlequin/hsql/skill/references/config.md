# Config files and profiles

Read this before you put a credential on a command line, and whenever you need to write
or repair a profile. `hsql` and `harlequin` read the same files, so one profile serves both — which is what makes "hand
this off to a human" in section 9 of the skill a one-liner.

## Where the files are, and which one wins

`hsql --info` reports the ones this machine actually has, highest priority first. The
search order is: the file `--config-path PATH` names, then the working directory, then
the user config directory, then the home directory. Within a directory,
`harlequin.toml` beats `.harlequin.toml`, which beats a `pyproject.toml`'s
`[tool.harlequin]` table.

Files merge **per profile**, nearest first: a project-local file that defines one profile
leaves every other profile, and the `default_profile` naming one of them, alone.

## What a profile looks like

```toml
default_profile = "dev"

[profiles.dev]
adapter = "duckdb"
conn_str = [ "./dev.db" ]

[profiles.prod]
adapter = "postgres"
host = "${PGHOST:-localhost}"     # ${VAR:-default} supplies a default
port = 5432                       # written as TOML makes natural; hsql coerces
user = "reporting"
password = "${PGPASSWORD}"        # ${VAR} is required; hsql exits 2 if unset
read_only = true
timeout = 30
limit = -1
```

Keys are the long option names with the dashes turned into underscores: `--read-only`
becomes `read_only`, `-P`'s own `--profile` is not a key (it names the profile). Two
groups of keys live here: hsql's own, and the connection options the adapter declares.
`hsql --help -a postgres` lists the second group; `hsql --spec` gives both as JSON.

Write `$${` if a value really has to start with a literal `${`.

Values an adapter declares as secret are masked wherever hsql prints them —
`--config show`, `--info`, and error messages — as is the password inside a connection
string.

Select a profile with `-P NAME`. `-P None` skips the config files entirely and runs on
Harlequin's own defaults. A CLI flag beats the profile only when you actually typed it.

## The five `--config` modes

None of them connects to a database.

```bash
hsql --config list-profiles     # the names -P takes, each one's adapter, which is default
hsql --config show              # the merged config, with the file each value came from
hsql --config show --json       # the same, for a parser
hsql --config validate          # every problem in every file; exits 2 if it found any
hsql --config schema            # a JSON Schema covering the adapters you have installed
hsql --config init -P prod -a sqlite ./app.db --read-only --limit -1
```

`init` is the one that writes. It takes the options you typed — hsql's and the
adapter's alike — writes them into `[profiles.prod]` in the nearest config file, prompts
for nothing, and leaves the other profiles and the file's comments untouched. It is the right way to create a profile
non-interactively; `harlequin --config` is the interactive wizard for a human.

`list-profiles` and `validate` are result sets, so `--csv`, `-t`, `-A` and `-o` work on
them the way they work on a query.

Point an editor at the schema for completion as you type:

```bash
hsql --config schema -o ./harlequin-schema.json
```

## Writing a config file by hand

1. `hsql --info` — see which files exist and which would win.
2. `hsql --help -a NAME` — see what that adapter's connection options are called.
3. Write the profile, with `${VAR}` for anything secret.
4. `hsql --config validate` — it names the file and the key for anything wrong,
   including a misspelled key that an adapter would otherwise have silently ignored.
5. `hsql -P NAME --catalog` — the cheapest proof that the profile connects.

## The same profile in the IDE

```bash
harlequin -P prod
```

`harlequin` reads these files identically. Keys that only mean something to the IDE — a
theme, a keymap — sit in the same profile and are ignored by hsql, and hsql's own keys
are ignored by the IDE, so one profile can carry both.
