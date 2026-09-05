# CLI options: the groups, and how to add one

Harlequin and `hsql` share their option machinery, and `hsql` additionally
sorts every option into a **group**. This document is the reference for that,
and the checklist for adding an option.

## Where an option comes from

There are two kinds, and they are declared in different places.

**An adapter's options** are `AbstractOption` subclasses in the adapter's own
`ADAPTER_OPTIONS` list. Declaring one gets all three renderings —
`to_click()` for the CLI, `to_widgets()` for the TUI, `to_questionary()` for
the config wizard — and both commands attach them dynamically once the first
pass has named the adapter. Nothing in core lists them.

**A command's own options** are `@click.option` decorators: `harlequin`'s in
`harlequin/cli.py`, `hsql`'s in `harlequin/hsql/cli.py`. These are the frozen
part of each command's surface; an adapter option whose spelling collides with
one loses that spelling (`first_pass.attach_adapter_options`).

Every option that is not `CONN_STR` is also a **profile key**, under the name
click gives it (`--read-only` is `read_only`). `config.py` validates a profile
against the command's parameters plus the adapter's declared options, so a key
neither declares is an error.

## The five groups

`hsql` runs in two roles — a server (`--serve NAME`) and a client
(`--session NAME`) — and an option belongs to exactly one group, decided by
**when the question it answers can be answered**. The groups are frozensets at
the top of `harlequin/hsql/cli.py`.

| Group | Answered | `--serve` | Served request |
| --- | --- | --- | --- |
| `CONNECTION_OPTIONS` | once, when the connection opens | accepted | compared against the session's; equal is served, differing exits 2 |
| `PER_REQUEST_OPTIONS` | on every invocation | refused, exit 2 | accepted |
| `SERVER_OPTIONS` | once, and only a server has one | accepted | refused, exit 2 |
| `ROLE_OPTIONS` | which process this invocation is | — | — |
| `CONFIG_OPTIONS` | which file and profile the rest come from | accepted | accepted |

Two of these need a word.

**`CONFIG_OPTIONS` (`-P`, `--config-path`) name where values come from rather
than being values**, so which group a profile belongs to is decided by what it
holds. A profile of nothing but `format` and `limit` is per-request whichever
way it was named; one that names a database is compared like a typed flag.

**Every adapter option is a connection option.** `connection_option_names()`
is the join: `CONNECTION_OPTIONS` plus the adapter's declared names. It is a
*positive* test — a key that is neither hsql's nor the adapter's, such as one
of `TUI_ONLY_KEYS` or a misspelling in a profile, describes no connection and
is not compared.

## Adding an option

1. **Decide whose it is.** If it changes how a database is reached, it is an
   adapter option — declare it in that adapter's `ADAPTER_OPTIONS` and stop.
   Everything below is for a core option on `harlequin` or `hsql`.

2. **Write the `@click.option`.** For `hsql`, keep the help one or two
   sentences and name the default.

3. **Put its name in exactly one group** in `harlequin/hsql/cli.py`. Ask when
   its question can be answered:
   - once, at connect time → `CONNECTION_OPTIONS`
   - on each invocation, including every mode → `PER_REQUEST_OPTIONS`
   - once, for a server that is up → `SERVER_OPTIONS`

   `tests/unit_tests/test_hsql_serve.py::test_every_option_is_in_exactly_one_group`
   fails if you skip this, or if you put it in two.

4. **If it is a mode** — one that reports rather than running SQL — also add
   it to `_one_mode()`, so two modes in one invocation are refused, and to the
   `extra_options` and the `needs_profile` / `needs_adapter` predicates in
   `build_cli()`, so the first pass does not read a profile for it.

5. **If a config file may not set it**, add it to `CLI_ONLY_SESSION_KEYS` or
   `CLI_ONLY_SSH_KEYS` in `config.py`. That is for options whose value decides
   *which process runs the invocation*, or which a config file discovered in
   the working directory should not be able to weaken.

6. **If the IDE reads it and `hsql` must not**, add it to `TUI_ONLY_KEYS`.

7. **Regenerate the committed artifacts**, which are pinned by tests:

   ```bash
   uv run python scripts/write_config_schema.py
   uv run python scripts/write_cli_reference.py
   ```

8. **Add a `CHANGELOG.md` entry** under `[Unreleased]` if the option is
   user-facing.

## Known weakness

Group membership lives in a name list beside the declaration rather than on
it, so the two can drift. The partition test is what catches the drift for a
*declared* option, which is why it exists and why it should not be relaxed.
It cannot catch a profile key that no command declares; that class is handled
instead by `connection_option_names()` being a positive test.

The alternative — carrying the group on the `click.Parameter` itself, so it
cannot be declared without one — is a larger change to both commands and has
not been made.
