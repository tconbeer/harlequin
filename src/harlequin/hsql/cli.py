"""The `hsql` command: run SQL against any Harlequin adapter, and exit.

Two-phase parsing is what keeps start-up cheap. The first pass -- shared with
the IDE, in `harlequin.first_pass` -- reads `-a`, `-P` and `--config-path` well
enough to name the one adapter an invocation will use, without importing any of
them; the second builds the real command with that adapter's connection options
on it. An invocation only ever uses one adapter,
so for execution this is not a compromise.

`--help` and `--version` are the exception, and work the other way round: with
no adapter named they answer without importing one at all -- help renders the
adapter-agnostic surface plus the *names* of what is installed. `hsql --help -a
postgres` imports postgres alone. That keeps the first thing a caller reads
small and stable, and keeps it true for every adapter rather than for whichever
one is the default.

A **mode** is the third shape. `--config MODE` reports on the config files --
or, under `init`, writes a profile into one -- `--spec` reports on the command
itself and `--info` on the installation, rather than any of them running SQL, so
the first pass skips the profile. It names no adapter either, and the command
carries no connection options at all, except for `--config init`: the options it
writes into a profile are the ones an adapter declares. Modes are options rather
than subcommands because `CONN_STR` is positional: `hsql catalog` and a DuckDB
file named `catalog` would have needed a rule, and `--catalog` needs none. They are
mutually exclusive, and each lives in `harlequin.hsql.modes`, imported by the
callback when it is chosen.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Iterator, Mapping, Sequence

import click

from harlequin.config import (
    DEFAULT_ADAPTER,
    TUI_ONLY_KEYS,
    UNLIMITED,
    merge_profile_with_cli,
    parse_profile_options,
    parse_row_count,
)
from harlequin.exception import (
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinError,
)
from harlequin.first_pass import (
    attach_adapter_options,
    command_spellings,
    first_pass,
)
from harlequin.hsql import diagnostics, output
from harlequin.hsql.diagnostics import ExitCode
from harlequin.hsql.modes import CONFIG_MODES, INIT
from harlequin.plugins import adapter_names, load_adapter

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinAdapter, HarlequinConnection
    from harlequin.layout import LayoutOptions
    from harlequin.query import ExecutedStatement, OnError, ResultSet, RowLimit
    from harlequin.statements import Statement

PROGRAM = "hsql"

DEFAULT_FORMAT = "table"

DEFAULT_LIMIT = 500
"""Small on purpose: fetching a million rows to print forty of them is waste.

`--limit` is the *hard* limit -- `cursor.set_limit()`, so fewer rows leave the
database -- and it is the same promise the `limit` key makes in the IDE. `-1`
is unlimited, and `0` fetches a header and no rows, which is how a caller asks
what a query's columns are.
"""

SHORTHANDS = {
    "csv": "csv",
    "json": "json",
    "jsonl": "jsonl",
    "markdown": "markdown",
    "vertical": "vertical",
}
"""`--csv` and friends, as flags, spelling the `--format` they stand for."""

SOURCES = f"{__name__}.sources"
"""Context key under which `-c` and `-f` record themselves, in order."""


def build_cli(argv: Sequence[str]) -> click.Command:
    """Build the hsql command, importing at most one adapter to do it.

    Takes the same arguments click is about to parse, because which adapter's
    connection options belong on the command is a question only the arguments
    can answer.
    """
    installed = adapter_names()
    found = first_pass(
        argv,
        installed,
        program=PROGRAM,
        # the modes, so that the pass can decline to read a profile for a
        # command that is not going to connect with one
        extra_options=[
            click.Option(["--config"]),
            click.Option(["--spec"], is_flag=True),
            click.Option(["--info"], is_flag=True),
        ],
        # A mode reports on what is installed or configured rather than
        # connecting with it, so there is no profile to read for one. `--config`
        # reads the files itself and reports what it finds wrong with them under
        # its own exit code, rather than having the first pass hold an error
        # about the first file it stumbled on, and `--info` reports one as part
        # of its answer; `--spec` describes the command, which a config file it
        # could not read has no bearing on.
        needs_profile=lambda params: (
            not (
                params.get("config") is not None
                or params.get("spec")
                or params.get("info")
            )
        ),
        # `--config init` is the mode that needs an adapter without a profile:
        # the profile it names is the one it is about to write, so reading one
        # would refuse the invocation over a name that does not exist yet, and
        # the adapter's options are half of what it writes.
        needs_adapter=lambda params: (
            not (
                _reports_config(params.get("config"))
                or params.get("spec")
                or params.get("info")
            )
        ),
        # bare `hsql` renders help, so it names no adapter either
        no_args_is_help=True,
    )

    profile_config = found.profile
    adapter_cls: type[HarlequinAdapter] | None = None
    setup_error: HarlequinConfigError | None = found.error
    if found.adapter is not None and setup_error is None:
        try:
            adapter_cls = load_adapter(found.adapter)
        except HarlequinConfigError as e:
            setup_error = e

    @click.command(
        name=PROGRAM,
        no_args_is_help=True,
        epilog=_epilog(installed, found.adapter),
    )
    @click.version_option(package_name="harlequin", prog_name=PROGRAM)
    @click.argument("conn_str", nargs=-1)
    @click.option(
        "-c",
        "--command",
        multiple=True,
        callback=_record_source,
        help="Execute SQL. Repeatable.",
    )
    @click.option(
        "-f",
        "--file",
        multiple=True,
        callback=_record_source,
        metavar="PATH",
        help="Execute SQL from a file, or from stdin for `-`. Repeatable.",
    )
    @click.option(
        "-o",
        "--output",
        type=click.Path(dir_okay=False, path_type=Path),
        metavar="PATH",
        help="Write results to PATH instead of stdout.",
    )
    # long spelling only: -F is psql's --field-separator, and a flag that sets
    # a delimiter in one command and picks a format in the other is a mistake
    # waiting for a script. The shorthand flags below cover the common choices.
    @click.option(
        "--format",
        default=DEFAULT_FORMAT,
        show_default=True,
        metavar="NAME",
        type=click.Choice(output.format_names(), case_sensitive=False),
        help="Output format. See below for the list.",
    )
    @click.option("--csv", is_flag=True, help="Shorthand for --format csv.")
    @click.option("--json", is_flag=True, help="Shorthand for --format json.")
    @click.option("--jsonl", is_flag=True, help="Shorthand for --format jsonl.")
    @click.option("--markdown", is_flag=True, help="Shorthand for --format markdown.")
    @click.option("--vertical", is_flag=True, help="Shorthand for --format vertical.")
    @click.option(
        "-t",
        "--tuples-only",
        is_flag=True,
        help="Rows only: no header, no footer. As in psql.",
    )
    @click.option(
        "-A", "--no-align", is_flag=True, help="Unaligned output. As in psql."
    )
    @click.option(
        "--no-header", is_flag=True, help="Omit the header row, keeping other chrome."
    )
    @click.option(
        "--no-footer",
        is_flag=True,
        help="Omit the row-count footer, keeping other chrome.",
    )
    @click.option(
        "--null-string",
        metavar="TEXT",
        help="Render NULL as TEXT. Defaults to NULL for text formats, empty for csv.",
    )
    @click.option(
        "-P",
        "--profile",
        help=(
            "Load a profile from an available config file. Options passed here take "
            "precedence over the profile's. Use the profile named None for "
            "Harlequin's defaults instead of the config file's default profile."
        ),
    )
    @click.option(
        "-a",
        "--adapter",
        default=DEFAULT_ADAPTER,
        show_default=True,
        metavar="NAME",
        type=click.Choice(installed, case_sensitive=False),
        help="The installed adapter plug-in to connect with.",
    )
    # existence is not click's to check: every mode that reads this path
    # already refuses a file that is not there, naming it, and `--config init`
    # is the one invocation whose whole job is to write a file that is not there
    # yet.
    @click.option(
        "--config-path",
        type=click.Path(dir_okay=False, resolve_path=True, path_type=Path),
        envvar="HARLEQUIN_CONFIG_PATH",
        show_envvar=True,
        metavar="PATH",
        help=(
            "Read this config file instead of the ones hsql discovers. "
            "--config init writes it."
        ),
    )
    @click.option(
        "--config",
        "config_mode",
        metavar="MODE",
        type=click.Choice(CONFIG_MODES, case_sensitive=False),
        help=(
            "Report on the config files hsql found, or write a profile into "
            "one, and exit without running SQL. One of: "
            f"{', '.join(CONFIG_MODES)}."
        ),
    )
    @click.option(
        "--spec",
        is_flag=True,
        help=(
            "Every option here, plus every installed adapter's, as JSON. "
            "-a narrows it to one adapter."
        ),
    )
    @click.option(
        "--info",
        is_flag=True,
        help=(
            "Versions, config files, the active profile, and what each "
            "installed adapter declares it supports, as JSON. Connects to "
            "nothing. -a narrows it to one adapter."
        ),
    )
    @click.option(
        "--limit",
        default=DEFAULT_LIMIT,
        show_default=True,
        metavar="N",
        type=click.IntRange(min=UNLIMITED),
        help="Maximum rows fetched per result set. -1 for no limit.",
    )
    @click.option(
        "--display-rows",
        metavar="N",
        type=click.IntRange(min=UNLIMITED),
        help=(
            "Rows printed per result set by the text layouts. -1 for all rows. "
            f"[default: {_default_display_rows()}]"
        ),
    )
    @click.option(
        "--result",
        default="all",
        show_default=True,
        metavar="all|last|N",
        help="Which result set(s) to emit.",
    )
    @click.option(
        "--on-error",
        default="stop",
        show_default=True,
        type=click.Choice(["stop", "continue"]),
        help="What to do when a statement fails.",
    )
    @click.option(
        "--stats", is_flag=True, help="Write a one-line JSON summary to stderr."
    )
    @click.option(
        "--color",
        default="never",
        show_default=True,
        type=click.Choice(["auto", "always", "never"]),
        help="Color text output. `auto` follows the terminal and NO_COLOR.",
    )
    @click.pass_context
    def inner_cli(
        ctx: click.Context,
        profile: str | None,
        config_path: Path | None,
        config_mode: str | None,
        spec: bool,
        info: bool,
        **kwargs: Any,
    ) -> None:
        """Execute SQL and exit.

        CONN_STR: one or more connection strings, or paths to local db files.
        """
        # `profile`, `config_path` and the mode flags are named rather than left
        # in `kwargs` so that they stay out of the merge, and so out of the
        # options handed to the adapter. The first pass has already read them off
        # the same argv -- a mode is what decides there is no adapter to
        # attach options for.
        # the profile was already read, and the adapter already loaded, to
        # decide which connection options this command carries. Whatever went
        # wrong doing that is reported here, where there is an exit code.
        if setup_error is not None:
            diagnostics.report_error(setup_error)
            ctx.exit(ExitCode.USAGE)
        explicitly_set = {
            k
            for k in kwargs
            if ctx.get_parameter_source(k) != click.core.ParameterSource.DEFAULT
        }
        values: dict[str, Any] = dict(
            merge_profile_with_cli(
                profile=profile_config,
                cli_values=kwargs,
                explicitly_set=explicitly_set,
            )
        )
        for key in TUI_ONLY_KEYS:
            values.pop(key, None)

        # every key hsql owns comes off here; whatever is left is the adapter's
        conn_str: Sequence[str] | str = values.pop("conn_str", tuple())
        if isinstance(conn_str, str):
            conn_str = (conn_str,)
        adapter: str = values.pop("adapter", DEFAULT_ADAPTER)
        destination: Path | None = values.pop("output", None)
        result_spec: str = str(values.pop("result", "all"))
        raw_on_error = str(values.pop("on_error", "stop"))
        if raw_on_error not in ("stop", "continue"):
            # click's Choice already vetted the flag; a profile can say anything
            diagnostics.error(f"on_error takes stop or continue, not {raw_on_error}.")
            ctx.exit(ExitCode.USAGE)
        on_error: OnError = "continue" if raw_on_error == "continue" else "stop"
        stats: bool = bool(values.pop("stats", False))
        tuples_only: bool = bool(values.pop("tuples_only", False))
        no_align: bool = bool(values.pop("no_align", False))
        no_header: bool = bool(values.pop("no_header", False))
        no_footer: bool = bool(values.pop("no_footer", False))
        null_string: str | None = values.pop("null_string", None)
        color_when: str = str(values.pop("color", "never"))
        raw_limit = values.pop("limit", DEFAULT_LIMIT)
        raw_display_rows = values.pop("display_rows", None)

        format_name = _resolve_format(values, explicitly_set)
        if format_name is None:
            ctx.exit(ExitCode.USAGE)

        sources: list[tuple[str, tuple[str, ...]]] = ctx.meta.get(SOURCES, [])

        mode = _one_mode(ctx, config_mode=config_mode, spec=spec, info=info)
        if mode is not None and sources:
            # a mode does not run SQL, so `-c` or `-f` beside one is two
            # invocations spelled as one, and refusing is the safer half of the
            # choice: answering the mode's question and dropping the query would
            # leave a script believing it had run one.
            diagnostics.error(
                f"{mode} does not run SQL; drop -c/--command and -f/--file, "
                f"or drop {mode.split()[0]}."
            )
            ctx.exit(ExitCode.USAGE)

        if info:
            ctx.exit(
                _report_info(
                    ctx,
                    # `-a` narrows the document to one adapter; its default
                    # names duckdb, and defaulting is not asking
                    adapter=adapter if "adapter" in explicitly_set else None,
                    profile_name=profile,
                    config_path=config_path,
                    destination=destination,
                    format_name=format_name,
                    format_chosen=format_name != DEFAULT_FORMAT
                    or "format" in explicitly_set,
                )
            )

        if spec:
            ctx.exit(
                _report_spec(
                    ctx,
                    # `-a` narrows the document to one adapter; its default
                    # names duckdb, and defaulting is not asking
                    adapter=adapter if "adapter" in explicitly_set else None,
                    destination=destination,
                    format_name=format_name,
                    format_chosen=format_name != DEFAULT_FORMAT
                    or "format" in explicitly_set,
                )
            )

        if config_mode is not None:
            # a shorthand flag and a profile's `format` key are choices too, and
            # both modes read this: the reporting ones to explain a format that
            # had no effect, and `init` to write the format that was asked for
            format_chosen = format_name != DEFAULT_FORMAT or "format" in explicitly_set
            if _reports_config(config_mode):
                ctx.exit(
                    _report_config(
                        ctx,
                        config_mode,
                        config_path=config_path,
                        destination=destination,
                        format_name=format_name,
                        format_chosen=format_chosen,
                        display_rows=raw_display_rows,
                        tuples_only=tuples_only,
                        no_align=no_align,
                        no_header=no_header,
                        no_footer=no_footer,
                        null_string=null_string,
                        color=_use_color(color_when, destination),
                    )
                )
            ctx.exit(
                _write_config(
                    ctx,
                    profile_name=profile,
                    adapter=adapter,
                    values=_typed_profile_keys(
                        kwargs,
                        explicitly_set,
                        format_name=format_name,
                        format_chosen=format_chosen,
                    ),
                    config_path=config_path,
                )
            )

        if not sources:
            diagnostics.error(
                "no SQL to run. Pass -c/--command or -f/--file, "
                f"or see '{PROGRAM} --help'."
            )
            ctx.exit(ExitCode.USAGE)

        try:
            statements = _read_statements(sources)
        except OSError as e:
            diagnostics.error(f"could not read {e.filename}: {e.strerror}")
            ctx.exit(ExitCode.USAGE)

        # after the usage checks above, so that a run with nothing to run says
        # only that, rather than first remarking on flags it never reached
        try:
            limit = parse_row_count(raw_limit, key="--limit")
            display_limit = _display_limit(raw_display_rows, format_name)
        except HarlequinConfigError as e:
            diagnostics.report_error(e)
            ctx.exit(ExitCode.USAGE)

        from harlequin.query import RowLimit

        row_limit = RowLimit(
            max_rows=limit,
            # one row more than we intend to keep is the only way to know a
            # result was cut short: set_limit(n) then fetchall() returns at most
            # n rows and says nothing about an n+1th.
            detect_overflow=limit is not None,
        )

        if "tuples_only" in explicitly_set:
            # ahead of the connection rather than after it: `-t nord` does not
            # reliably fail, so there may be no error for this to explain.
            diagnostics.report_theme_confusion(conn_str)

        try:
            adapter_instance = load_adapter(adapter)(conn_str=conn_str, **values)
        except HarlequinConfigError as e:
            diagnostics.report_error(e)
            ctx.exit(ExitCode.USAGE)

        connection: HarlequinConnection
        try:
            connection = adapter_instance.connect()
        except HarlequinConnectionError as e:
            diagnostics.report_error(e)
            ctx.exit(ExitCode.CONNECTION)

        run = _Run()
        executed = _execute_all(
            connection, statements, limit=row_limit, on_error=on_error, run=run
        )

        selected = _select_results(executed, result_spec)
        if selected is None:
            ctx.exit(ExitCode.USAGE)
        if len(selected) > 1 and not output.holds_many(format_name):
            n = len(selected)
            diagnostics.error(
                f"{n} result sets, but {format_name} holds one; "
                f"use --result last or --result {n}"
            )
            ctx.exit(ExitCode.USAGE)

        layout_options, file_options = _output_options(
            tuples_only=tuples_only,
            no_align=no_align,
            no_header=no_header,
            no_footer=no_footer,
            null_string=null_string,
            color=_use_color(color_when, destination),
            format_name=format_name,
            display_limit=display_limit,
        )

        try:
            with _sink(destination) as out:
                _emit(
                    selected,
                    out,
                    limit=row_limit,
                    format_name=format_name,
                    layout_options=layout_options,
                    file_options=file_options,
                    on_error=on_error,
                    run=run,
                )
        except OSError as e:
            diagnostics.report_error(e)
            ctx.exit(ExitCode.USAGE)

        if stats:
            diagnostics.report_stats(
                status=run.status,
                statements=run.statements,
                rows=run.rows,
                truncated=run.truncated,
                limit=row_limit.max_rows,
                elapsed_ms=run.elapsed_ms,
                columns=run.columns,
                message=_message_for(run.failure),
            )

        ctx.exit(run.exit_code)

    cmd: click.Command = inner_cli
    if adapter_cls is not None and found.adapter is not None:
        # what is left of the profile once this command's own options are out
        # of it belongs to the adapter, which is about to be handed it
        try:
            profile_config = parse_profile_options(
                profile_config,
                adapter=found.adapter,
                adapter_options=adapter_cls.ADAPTER_OPTIONS,
                command_options={param.name for param in cmd.params}
                | set(TUI_ONLY_KEYS),
            )
        except HarlequinConfigError as e:
            setup_error = e
        reserved, taken = command_spellings(cmd)
        # hsql's own flags are the part of it that is an API, so a colliding
        # adapter option loses the colliding spelling rather than shadowing a
        # documented one. The IDE, whose adapter options predate the rule,
        # attaches without reserving anything.
        attach_adapter_options(cmd, adapter_cls, reserved=reserved, taken=taken)
    return cmd


def _one_mode(
    ctx: click.Context, *, config_mode: str | None, spec: bool, info: bool
) -> str | None:
    """The one mode this invocation chose, or exit having named the two it did.

    Modes are options rather than subcommands, so nothing about the parse stops
    a caller passing two of them -- and two questions in one invocation has no
    answer that is not a guess about which they meant.
    """
    asked = (
        (f"--config {config_mode}", config_mode is not None),
        ("--spec", spec),
        ("--info", info),
    )
    chosen = [name for name, was_asked in asked if was_asked]
    if len(chosen) > 1:
        diagnostics.error(
            f"{chosen[0]} and {chosen[1]} are two modes; pass one of them."
        )
        ctx.exit(ExitCode.USAGE)
    return chosen[0] if chosen else None


def _report_info(
    ctx: click.Context,
    *,
    adapter: str | None,
    profile_name: str | None,
    config_path: Path | None,
    destination: Path | None,
    format_name: str,
    format_chosen: bool,
) -> ExitCode:
    """Answer `--info` and return its code, or exit having said why not.

    Exit 0 for a document it wrote: a config file it could not parse and an
    adapter that will not import are both facts this mode reports, rather than
    this invocation's failure.
    """
    # here rather than at module scope, for the reason the mode exists in its
    # own module: it imports every installed adapter, which no other invocation
    # should pay for
    from harlequin.hsql.modes import info as info_mode

    try:
        with _sink(destination) as out:
            code = info_mode.report(
                out,
                adapter=adapter,
                profile_name=profile_name,
                config_path=config_path,
                format_name=format_name,
                format_chosen=format_chosen,
            )
            out.flush()
    except (HarlequinConfigError, OSError) as e:
        # a `--config-path` naming no file, or a `-o PATH` it could not write.
        # Both are the caller's to fix, and both are usage errors.
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    return code


def _report_spec(
    ctx: click.Context,
    *,
    adapter: str | None,
    destination: Path | None,
    format_name: str,
    format_chosen: bool,
) -> ExitCode:
    """Answer `--spec` and return its code, or exit having said why not.

    Exit 0 for a document it wrote: this mode reports the CLI surface rather
    than judging anything, and an adapter it could not import is reported in the
    document rather than as this invocation's failure.
    """
    # here rather than at module scope, for the reason the mode exists in its
    # own module: it imports every installed adapter, which no other invocation
    # should pay for
    from harlequin.hsql.modes import spec as spec_mode

    try:
        with _sink(destination) as out:
            code = spec_mode.report(
                out,
                adapter=adapter,
                format_name=format_name,
                format_chosen=format_chosen,
            )
            out.flush()
    except OSError as e:
        # a `-o PATH` it could not write, which is the caller's to fix
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    return code


def _report_config(
    ctx: click.Context,
    mode: str,
    *,
    config_path: Path | None,
    destination: Path | None,
    format_name: str,
    format_chosen: bool,
    display_rows: Any,
    **output_options: Any,
) -> ExitCode:
    """Answer a `--config MODE` and return its code, or exit having said why not.

    The code is the mode's own: `--config validate` exits 2 for a config it
    found something wrong with, and the modes that only report exit 0.
    """
    # here rather than at module scope: this is the one invocation in ten
    # thousand that wants it, and every other one would pay the import
    from harlequin.hsql.modes import config as config_mode

    try:
        display_limit = _display_limit(display_rows, format_name)
        layout_options, file_options = _output_options(
            format_name=format_name, display_limit=display_limit, **output_options
        )
        with _sink(destination) as out:
            code = config_mode.report(
                mode,
                out,
                config_path=config_path,
                format_name=format_name,
                format_chosen=format_chosen,
                layout_options=layout_options,
                file_options=file_options,
            )
            out.flush()
    except (HarlequinConfigError, OSError) as e:
        # a config file this could not read, or a `-o PATH` it could not write.
        # Both are the caller's to fix, and both are usage errors.
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    return code


def _reports_config(mode: Any) -> bool:
    """Whether a `--config MODE` reads config files rather than writing one.

    Four of the five report, and need neither an adapter nor a profile; `init`
    writes, and needs the adapter whose options it is writing.
    """
    return mode is not None and str(mode).lower() != INIT


def _typed_profile_keys(
    cli_values: Mapping[str, Any],
    explicitly_set: set[str],
    *,
    format_name: str,
    format_chosen: bool,
) -> dict[str, Any]:
    """Every option the caller typed, under the key a profile spells it with.

    An option left at its default carries no intent -- the rule
    `merge_profile_with_cli()` reads the other way round when a profile meets a
    command line -- so `--config init` writes what was asked for rather than a
    copy of the command's defaults. The shorthand format flags are written as
    the `--format` they stand for, because `format` is the key a profile has.
    """
    typed = dict(
        merge_profile_with_cli(
            profile={}, cli_values=cli_values, explicitly_set=explicitly_set
        )
    )
    for flag in SHORTHANDS:
        typed.pop(flag, None)
    typed.pop("format", None)
    # written from the mode's own argument instead, so that it is there whether
    # or not the caller named one
    typed.pop("adapter", None)
    if format_chosen:
        typed["format"] = format_name
    # first among what a profile sets, as it is on the command line, because it
    # is the key a reader looks for to know what the profile connects to
    conn_str = typed.pop("conn_str", None)
    return typed if conn_str is None else {"conn_str": conn_str, **typed}


def _write_config(
    ctx: click.Context,
    *,
    profile_name: str | None,
    adapter: str,
    values: Mapping[str, Any],
    config_path: Path | None,
) -> ExitCode:
    """Answer `--config init` and return its code, or exit having said why not.

    Nothing reaches stdout: this mode writes a file, and what it wrote is on
    stderr with the file's name beside it. Which is also why every other option
    on the command line is a key in the profile rather than a way of reporting
    -- `-o` and `--format` included.
    """
    # here rather than at module scope, for the reason the mode exists in its
    # own module: this is the one invocation that writes a config file, and so
    # the only one that pays for tomlkit
    from harlequin.hsql.modes import config as config_mode

    try:
        config_mode.initialize(
            profile_name=profile_name,
            adapter=adapter,
            values=values,
            config_path=config_path,
        )
    except (HarlequinConfigError, OSError) as e:
        # a file this could not parse, a path it could not write, or a name no
        # profile may have. All three are the caller's to fix.
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    return ExitCode.OK


def bare_command() -> click.Command:
    """This command with no adapter's options on it, and no config file read.

    `harlequin` builds this to answer two questions about the other command:
    which spellings are hsql's, so it can point at them, and which profile keys
    hsql reads, so it does not mistake them for an adapter's options.
    """
    return build_cli(["-P", "None", "--help"])


@dataclass
class _Run:
    """What one invocation did, accumulated as it does it.

    Starts its own clock, so that "when did this run begin" cannot drift from
    "what has it done so far" by someone moving one of the two.
    """

    statements: int = 0
    """How many statements the database was asked to run."""

    rows: int = 0
    truncated: bool = False
    columns: list[tuple[str, str]] = field(default_factory=list)
    """The last emitted result set's columns, for `--stats`."""

    failure: BaseException | None = None
    """The last thing that went wrong, and so the code hsql exits with."""

    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed_ms(self) -> int:
        return round((time.monotonic() - self.started) * 1000)

    @property
    def status(self) -> str:
        return "error" if self.failure is not None else "ok"

    @property
    def exit_code(self) -> ExitCode:
        if self.failure is None:
            return ExitCode.OK
        return diagnostics.exit_code_for(self.failure)


def _execute_all(
    connection: HarlequinConnection,
    statements: Sequence[Statement],
    *,
    limit: RowLimit,
    on_error: OnError,
    run: _Run,
) -> list[ExecutedStatement]:
    """Run every statement, keeping the ones that produced a result set.

    Execute-then-fetch, in two passes, as the IDE does it: nothing is fetched
    until the whole script has been submitted, so `--result last` can decline
    to pay for the rest.
    """
    from harlequin.query import execute

    executed: list[ExecutedStatement] = []
    for item in execute(connection, statements, limit=limit, on_error=on_error):
        run.statements += 1
        if item.error is not None:
            run.failure = item.error
            diagnostics.report_error(item.error)
        elif item.has_result_set:
            executed.append(item)
    return executed


def _emit(
    selected: Sequence[ExecutedStatement],
    out: BinaryIO,
    *,
    limit: RowLimit,
    format_name: str,
    layout_options: LayoutOptions,
    file_options: Mapping[str, Any],
    on_error: OnError,
    run: _Run,
) -> None:
    """Fetch each selected result set and write it out."""
    from harlequin.query import fetch

    for item in selected:
        try:
            result = fetch(item, limit=limit)
        except Exception as e:  # noqa: BLE001 -- adapters are third-party code
            run.failure = e
            diagnostics.report_error(e)
            if on_error == "stop":
                break
            continue
        output.write(
            result,
            format_name,
            out,
            layout_options=layout_options,
            file_options=file_options,
        )
        run.rows += result.row_count
        run.truncated = run.truncated or result.truncated
        run.columns = result.columns
        if result.truncated and limit.max_rows is not None:
            diagnostics.report_truncation(limit.max_rows)
        _report_hidden_rows(result, layout_options)
    out.flush()


def _report_hidden_rows(result: ResultSet, layout_options: LayoutOptions) -> None:
    """Say on stderr that the row cap dropped rows, when nothing else can.

    Only with the footer suppressed: `40 of 500 rows` already says it, and this
    stream does not restate what stdout carries. `-t` is where it matters --
    that is the invocation whose output a script reads, and the one where the
    result would otherwise be short and silent about it.
    """
    cap = layout_options.max_rows
    if layout_options.footer or cap is None or result.row_count <= cap:
        return
    diagnostics.report_row_cap(shown=cap, of=result.fetched_row_count)


def _record_source(
    ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> tuple[str, ...]:
    """Keep `-c` and `-f` in the order they were typed.

    click hands a repeatable option all of its values at once, and processes
    the two options in the order they first appear -- which is what
    `-f setup.sql -c 'select ...'` depends on.
    """
    if value:
        ctx.meta.setdefault(SOURCES, []).append((param.name, value))
    return value


def _read_statements(
    sources: Sequence[tuple[str, tuple[str, ...]]],
) -> list[Statement]:
    """Every statement the invocation asked for, in order.

    Each source is split on its own, so two `-c` values are two statements
    whether or not either ends in a semicolon.
    """
    from harlequin.statements import Statement, split

    statements: list[Statement] = []
    for kind, values in sources:
        for value in values:
            if kind == "command":
                text = value
            elif value == "-":
                text = click.get_text_stream("stdin").read()
            else:
                text = Path(value).expanduser().read_text(encoding="utf-8")
            for statement in split(text):
                statements.append(Statement(sql=statement.sql, index=len(statements)))
    return statements


def _resolve_format(values: dict[str, Any], explicitly_set: set[str]) -> str | None:
    """The one format this invocation writes, or None having said why not."""
    chosen = [name for flag, name in SHORTHANDS.items() if values.pop(flag, False)]
    named: str = str(values.pop("format", DEFAULT_FORMAT))

    if len(chosen) > 1:
        diagnostics.error(
            "more than one output format: "
            + ", ".join(f"--{flag}" for flag in sorted(chosen))
        )
        return None
    if chosen and "format" in explicitly_set:
        diagnostics.error(f"--{chosen[0]} and --format {named} are two output formats.")
        return None
    format_name = (chosen[0] if chosen else named).lower()
    if format_name not in output.format_names():
        diagnostics.error(
            f"{format_name} is not an output format. "
            f"Try one of: {', '.join(output.format_names())}."
        )
        return None
    return format_name


def _select_results(
    executed: Sequence[ExecutedStatement], spec: str
) -> list[ExecutedStatement] | None:
    """The result sets `--result` asked for, or None having said why not."""
    if spec == "all":
        return list(executed)
    if spec == "last":
        return list(executed[-1:])
    try:
        index = int(spec)
    except ValueError:
        diagnostics.error(f"--result takes all, last or a number, not {spec}.")
        return None
    if not 1 <= index <= len(executed):
        diagnostics.error(
            f"--result {index}, but this run produced {len(executed)} result sets."
        )
        return None
    return [executed[index - 1]]


def _display_limit(raw: Any, format_name: str) -> int | None:
    """How many rows the layout prints: the caller's number, or the layout's.

    None where the format has no layout to cap -- a csv or a parquet is written
    for a machine, and dropping rows out of a file is a different promise from
    not filling a screen with them.

    Raises: HarlequinConfigError if the value is not a whole number of rows.
    """
    from harlequin.layout import default_max_rows

    if not output.is_layout(format_name):
        if raw is not None:
            # asking for five rows and getting five hundred is the kind of
            # surprise this command is supposed to not have.
            diagnostics.report_row_cap_ignored(format_name)
        return None
    if raw is None:
        return default_max_rows(format_name)
    return parse_row_count(raw, key="--display-rows")


def _default_display_rows() -> str:
    """Each layout's own row cap, for `--help`."""
    from harlequin.layout import default_max_rows, layout_names

    by_rows: dict[int, list[str]] = {}
    for name in layout_names():
        by_rows.setdefault(default_max_rows(name), []).append(name)
    return "; ".join(
        f"{rows} for {', '.join(names)}" for rows, names in by_rows.items()
    )


def _output_options(
    *,
    tuples_only: bool,
    no_align: bool,
    no_header: bool,
    no_footer: bool,
    null_string: str | None,
    color: bool,
    format_name: str,
    display_limit: int | None = None,
) -> tuple[LayoutOptions, dict[str, Any]]:
    """psql's flag algebra, in the vocabulary each family of format speaks.

    `-t` is two independent switches rather than a mode, which is why `-tA`
    needs no special case: it never was one. `--no-header` and `--no-footer`
    are those same two switches, spelled one at a time.
    """
    from harlequin.layout import LayoutOptions

    header = not (tuples_only or no_header)
    layout_options = LayoutOptions(
        header=header,
        footer=not (tuples_only or no_footer),
        aligned=not no_align,
        null_string=null_string,
        color=color,
        max_rows=display_limit,
    )
    file_options: dict[str, Any] = {}
    if format_name in ("csv", "tsv"):
        # the only writers with a header or a null of their own to set
        file_options["header"] = header
        if null_string is not None:
            file_options["na_rep"] = null_string
    return layout_options, file_options


def _use_color(when: str, destination: Path | None) -> bool:
    """Whether to style text output.

    The default is `never`, so the bytes do not depend on what stdout happens
    to be. `auto` opts into that dependence, and honors NO_COLOR; `always` is
    the caller saying they meant it. A file never gets escapes either way.
    """
    if destination is not None or when == "never":
        return False
    if when == "always":
        return True
    return sys.stdout.isatty() and not os.getenv("NO_COLOR")


@contextlib.contextmanager
def _sink(destination: Path | None) -> Iterator[BinaryIO]:
    """Where result sets go: a path, or stdout.

    Binary in both cases. duckdb writes `\\n` and the layouts write `\\n`, and
    text-mode translation would turn that into `\\r\\n` on Windows for the same
    query that produced `\\n` everywhere else.
    """
    if destination is None:
        yield click.get_binary_stream("stdout")
    else:
        with destination.expanduser().open("wb") as f:
            yield f


def _message_for(failure: BaseException | None) -> str | None:
    if failure is None:
        return None
    return (
        failure.msg if isinstance(failure, HarlequinError) else str(failure)
    ) or type(failure).__name__


def _epilog(installed: Sequence[str], adapter: str | None) -> str:
    """The reference an agent reads once instead of guessing at twice.

    Adapter *names* rather than their options: the list stays one line longer
    per installed adapter instead of one option table longer, and it is true
    for all of them rather than for whichever is the default. `--spec` is what
    that trade costs a caller, so this is where it is offered: the same surface
    with every adapter's options filled in, for a reader that would rather
    parse it than read it.
    """
    formats = ", ".join(output.format_names())
    names = ", ".join(installed) if installed else "(none installed)"
    if adapter is None:
        adapters = (
            f"Installed adapters: {names}\n"
            f"Run '{PROGRAM} --help -a <adapter>' for one adapter's "
            "connection options."
        )
    else:
        adapters = (
            f"Installed adapters: {names}\nShowing {adapter}'s connection options."
        )
    blocks = [
        f"Formats:\n  {formats}",
        (
            "Limits:\n"
            "  --limit N         rows fetched from the database. -1 for all\n"
            "  --display-rows N  rows the text layouts print, of those fetched"
        ),
        (
            "Exit codes:\n"
            "  0 success           1 query error       2 usage/config error\n"
            "  3 connection error  4 timeout           130 interrupted"
        ),
        adapters,
        (
            "Machine-readable:\n"
            f"  {PROGRAM} --spec   this help as JSON, with every installed "
            "adapter's options\n"
            f"  {PROGRAM} --info   versions, config files, and what each "
            "adapter supports"
        ),
    ]
    # a lone \b tells click not to rewrap the paragraph that follows it
    return "\n\n".join(f"\b\n{block}" for block in blocks)
