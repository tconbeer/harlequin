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

**A command's own options** are `CoreOption` declarations in
`harlequin/core_options.py`, one list for both commands. Each says which
command takes it (`harlequin=On(...)`, `hsql=On(...)`, or both), how that
command spells it, and the flags that decide where its value may come from —
`group`, `cli_only`, and `first_pass`, which is whether the pass that names
the adapter reads it before there is a command to parse with.
`attach_core_options()` builds the click options from that list, in the order
it declares them, which is the order `hsql --help` lists them; the part only a
command can answer — a `click.Choice` of what is installed, a callback, help
naming a value computed at run time — arrives as `Supplied`. These are the
frozen part of each command's surface; an adapter option whose spelling
collides with one loses that spelling (`first_pass.attach_adapter_options`).

Every option that is not `CONN_STR` is also a **profile key**, under the name
click gives it (`--read-only` is `read_only`). `config.py` validates a profile
against the command's parameters plus the adapter's declared options, so a key
neither declares is an error.

## The five groups

`hsql` runs in two roles — a server (`--serve NAME`) and a client
(`--session NAME`) — and an option belongs to exactly one group, decided by
**when its value is read**. An option hsql declares cannot be declared without
one; the frozensets `hsql` refuses against are derived from the declarations.

| Group | Read | `--serve` | Served request |
| --- | --- | --- | --- |
| `Group.CONNECTION` | once, when the connection opens | accepted | compared against the session's; equal is served, differing exits 2 |
| `Group.PER_REQUEST` | on every invocation | refused, exit 2 | accepted |
| `Group.SERVER` | once, at start-up; only a server has one | accepted | refused, exit 2 |
| `Group.ROLE` | to pick which process runs the invocation | — | — |
| `Group.CONFIG` | to pick the file and profile the rest come from | accepted | accepted |

Two of these need a word.

**`Group.CONFIG` (`-P`, `--config-path`) names where values come from rather
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

2. **Write the `CoreOption`** in `harlequin/core_options.py`, among the ones it
   belongs with — where it sits in that list is where `hsql --help` lists it.
   Give it `harlequin=On()`, `hsql=On()`, or both, and put in `kwargs` what
   both commands share and in each `On` what only that command says. For
   `hsql`, keep the help one or two sentences and name the default.

3. **Give it a group**, by when its value is read:
   - once, at connect time → `Group.CONNECTION`
   - on each invocation, including every mode → `Group.PER_REQUEST`
   - once, for a server that is up → `Group.SERVER`

   An option `hsql` takes has to have one, and an option only the IDE takes has
   to have none —
   `tests/unit_tests/test_hsql_serve.py::test_every_option_is_in_exactly_one_group`
   pins the groups to the command they are refused against.

4. **If it is a mode** — one that reports rather than running SQL — also add
   it to `_one_mode()`, so two modes in one invocation are refused. A mode that
   reports on the installation or the config files declares `first_pass=True`
   and goes in the `needs_profile` / `needs_adapter` predicates in
   `build_cli()` too, so the first pass reads no profile for it; a mode that is
   configured like a run does not (`--catalog`, `--history`).

5. **If a config file may not set it**, mark it `cli_only=True`. That is for
   options whose value decides *which process runs the invocation*, or which a
   config file discovered in the working directory should not be able to
   weaken; `CLI_ONLY_SESSION_KEYS` and `CLI_ONLY_SSH_KEYS`, which are what
   refuse it, are derived from the flag.

6. **An option the IDE reads and `hsql` must not** is one with no `hsql=`:
   `TUI_ONLY_KEYS` is every declaration `harlequin` takes and `hsql` does not.

7. **Regenerate the committed artifacts**, which are pinned by tests:

   ```bash
   uv run python scripts/write_config_schema.py
   uv run python scripts/write_cli_reference.py
   ```

8. **Add a `CHANGELOG.md` entry** under `[Unreleased]` if the option is
   user-facing.

## What the partition test still catches

Group membership travels with the declaration, so the two cannot drift. What
the test reads is the built command against the derived sets, which is what
catches an option added to `harlequin/hsql/cli.py` as a bare `@click.option`
rather than declared — it would reach the command in no group at all, and
nobody would refuse it. It cannot catch a profile key that no command
declares; that class is handled instead by `connection_option_names()` being a
positive test.
