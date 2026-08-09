"""The `hsql` command: run SQL against any Harlequin adapter, and exit.

Two-phase parsing is what keeps start-up cheap. The first pass reads `-a`, `-P`
and `--config-path` well enough to name the one adapter an invocation will use,
without importing any of them; the second builds the real command with that
adapter's connection options on it. An invocation only ever uses one adapter,
so for execution this is not a compromise.

`--help` is the exception, and works the other way round: with no adapter named
it renders the adapter-agnostic surface plus the *names* of what is installed,
importing nothing at all. `hsql --help -a postgres` imports postgres alone.
That keeps the first thing a caller reads small and stable, and keeps it true
for every adapter rather than for whichever one is the default.
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

from harlequin.config import get_config_for_profile, merge_profile_with_cli
from harlequin.exception import (
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinError,
)
from harlequin.hsql import diagnostics, output
from harlequin.hsql.diagnostics import ExitCode
from harlequin.plugins import adapter_names, load_adapter

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinAdapter, HarlequinConnection
    from harlequin.layout import LayoutOptions
    from harlequin.query import ExecutedStatement, OnError, RowLimit
    from harlequin.statements import Statement

PROGRAM = "hsql"

# Repeated from harlequin.cli rather than imported: that module builds the IDE's
# command and reaches the app, the config wizard and the keymap editor with it.
DEFAULT_ADAPTER = "duckdb"

DEFAULT_FORMAT = "table"

DEFAULT_LIMIT = 500
"""Small on purpose. The IDE's 100,000 is right for a viewport and wrong here.

`hsql -l` is also the *hard* limit -- `cursor.set_limit()`, so fewer rows leave
the database -- where the IDE's is a soft cap over a full fetch. Same spelling,
different promise, and the docs say so.
"""

SHORTHANDS = {
    "csv": "csv",
    "json": "json",
    "jsonl": "jsonl",
    "markdown": "markdown",
    "vertical": "vertical",
}
"""`--csv` and friends, as flags, spelling the `-F` they stand for."""

TUI_ONLY_KEYS = (
    "theme",
    "keymap_name",
    "show_files",
    "show_s3",
    "locale",
    "no_download_tzdata",
)
"""Profile keys that belong to the IDE.

A profile is shared between the two commands, so a profile written for the IDE
has to work here -- these are dropped rather than handed to the adapter as
options it never declared. `locale` in particular is one hsql must ignore: the
IDE sets it to group digits for a human, and output that varied with `LC_ALL`
would be output a caller could not predict.
"""

SOURCES = f"{__name__}.sources"
"""Context key under which `-c` and `-f` record themselves, in order."""


def build_cli(args: Sequence[str] | None = None) -> click.Command:
    """Build the hsql command, importing at most one adapter to do it."""
    argv = list(args) if args is not None else sys.argv[1:]
    adapter_name, wants_help = _preflight(argv)
    installed = adapter_names()

    adapter_cls: type[HarlequinAdapter] | None = None
    adapter_error: HarlequinConfigError | None = None
    if adapter_name is not None:
        try:
            adapter_cls = load_adapter(adapter_name)
        except HarlequinConfigError as e:
            adapter_error = e

    @click.command(
        name=PROGRAM,
        no_args_is_help=True,
        epilog=_epilog(installed, adapter_name),
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
    @click.option(
        "-F",
        "--format",
        default=DEFAULT_FORMAT,
        show_default=True,
        metavar="NAME",
        type=click.Choice(output.format_names(), case_sensitive=False),
        help="Output format. See below for the list.",
    )
    @click.option("--csv", is_flag=True, help="Shorthand for -F csv.")
    @click.option("--json", is_flag=True, help="Shorthand for -F json.")
    @click.option("--jsonl", is_flag=True, help="Shorthand for -F jsonl.")
    @click.option("--markdown", is_flag=True, help="Shorthand for -F markdown.")
    @click.option("--vertical", is_flag=True, help="Shorthand for -F vertical.")
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
    @click.option(
        "--config-path",
        type=click.Path(exists=True, dir_okay=False, resolve_path=True, path_type=Path),
        envvar="HARLEQUIN_CONFIG_PATH",
        show_envvar=True,
        metavar="PATH",
        help="Read this config file instead of the ones hsql discovers.",
    )
    @click.option(
        "-l",
        "--limit",
        default=DEFAULT_LIMIT,
        show_default=True,
        metavar="N",
        type=click.IntRange(min=0),
        help="Maximum rows per result set. 0 for no limit.",
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
        **kwargs: Any,
    ) -> None:
        """Execute SQL and exit.

        CONN_STR: one or more connection strings, or paths to local db files.
        """
        if adapter_error is not None:
            diagnostics.report_error(adapter_error)
            ctx.exit(ExitCode.USAGE)

        try:
            config, _ = get_config_for_profile(
                config_path=config_path, profile_name=profile
            )
        except HarlequinConfigError as e:
            diagnostics.report_error(e)
            ctx.exit(ExitCode.USAGE)

        explicitly_set = {
            k
            for k in kwargs
            if ctx.get_parameter_source(k) != click.core.ParameterSource.DEFAULT
        }
        values: dict[str, Any] = dict(
            merge_profile_with_cli(
                profile=config, cli_values=kwargs, explicitly_set=explicitly_set
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
        null_string: str | None = values.pop("null_string", None)
        color_when: str = str(values.pop("color", "never"))
        try:
            limit = int(values.pop("limit", DEFAULT_LIMIT))
        except (TypeError, ValueError):
            diagnostics.error("--limit must be a whole number of rows, or 0.")
            ctx.exit(ExitCode.USAGE)

        format_name = _resolve_format(values, explicitly_set)
        if format_name is None:
            ctx.exit(ExitCode.USAGE)

        sources: list[tuple[str, tuple[str, ...]]] = ctx.meta.get(SOURCES, [])
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

        from harlequin.query import RowLimit

        row_limit = RowLimit(
            max_rows=limit or None,
            # one row more than we intend to keep is the only way to know a
            # result was cut short: set_limit(n) then fetchall() returns at most
            # n rows and says nothing about an n+1th.
            detect_overflow=bool(limit),
        )

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

        run_started = time.monotonic()
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
            null_string=null_string,
            color=_use_color(color_when, destination),
            format_name=format_name,
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
                status="error" if run.failure is not None else "ok",
                statements=run.statements,
                rows=run.rows,
                truncated=run.truncated,
                limit=row_limit.max_rows,
                elapsed_ms=round((time.monotonic() - run_started) * 1000),
                columns=run.columns,
                message=_message_for(run.failure),
            )

        ctx.exit(
            ExitCode.OK
            if run.failure is None
            else diagnostics.exit_code_for(run.failure)
        )

    cmd: click.Command = inner_cli
    if adapter_cls is not None:
        reserved = {opt for param in cmd.params for opt in param.opts}
        taken = {param.name for param in cmd.params if param.name is not None}
        cmd.params.extend(_adapter_params(adapter_cls, reserved, taken))
    return cmd


@dataclass
class _Run:
    """What one invocation did, accumulated as it does it."""

    statements: int = 0
    """How many statements the database was asked to run."""

    rows: int = 0
    truncated: bool = False
    columns: list[tuple[str, str]] = field(default_factory=list)
    """The last emitted result set's columns, for `--stats`."""

    failure: BaseException | None = None
    """The last thing that went wrong, and so the code hsql exits with."""


def _execute_all(
    connection: HarlequinConnection,
    statements: Sequence[Statement],
    *,
    limit: RowLimit,
    on_error: OnError,
    run: _Run,
) -> list[tuple[ExecutedStatement, float]]:
    """Run every statement, keeping the ones that produced a result set.

    Execute-then-fetch, in two passes, as the IDE does it: nothing is fetched
    until the whole script has been submitted, so `--result last` can decline
    to pay for the rest.
    """
    from harlequin.query import execute

    executed: list[tuple[ExecutedStatement, float]] = []
    started = time.monotonic()
    for item in execute(connection, statements, limit=limit, on_error=on_error):
        elapsed = time.monotonic() - started
        run.statements += 1
        if item.error is not None:
            run.failure = item.error
            diagnostics.report_error(item.error)
        elif item.has_result_set:
            executed.append((item, elapsed))
        started = time.monotonic()
    return executed


def _emit(
    selected: Sequence[tuple[ExecutedStatement, float]],
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

    for item, exec_elapsed in selected:
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
        diagnostics.report_row_count(
            result.row_count, exec_elapsed + result.elapsed, result.truncated
        )
    out.flush()


def _preflight(args: Sequence[str]) -> tuple[str | None, bool]:
    """Name the adapter an invocation will use, importing none of them.

    Best-effort by design: a config file it cannot read is not reported here,
    because the real parse is about to read the same file and has somewhere to
    report it. All this decides is whose connection options get attached.
    """
    wants_help = not args or "--help" in args

    probe = click.Command(
        PROGRAM,
        params=[
            click.Option(["-a", "--adapter"]),
            click.Option(["-P", "--profile"]),
            click.Option(
                ["--config-path"],
                type=click.Path(path_type=Path),
                envvar="HARLEQUIN_CONFIG_PATH",
            ),
        ],
        add_help_option=False,
    )
    # on the Context, not in the command's context_settings: those are applied
    # by make_context(), which this deliberately does not go through.
    ctx = click.Context(
        probe,
        resilient_parsing=True,
        ignore_unknown_options=True,
        allow_extra_args=True,
        allow_interspersed_args=True,
    )
    with contextlib.suppress(click.ClickException, ValueError):
        probe.parse_args(ctx, list(args))

    name: str | None = ctx.params.get("adapter")
    if name is None:
        with contextlib.suppress(HarlequinConfigError, OSError):
            found, _ = get_config_for_profile(
                config_path=ctx.params.get("config_path"),
                profile_name=ctx.params.get("profile"),
            )
            name = found.get("adapter")

    installed = {n.lower(): n for n in adapter_names()}
    if name is not None:
        # an unknown name is left for click's Choice to reject, with the list
        return installed.get(str(name).lower()), wants_help
    if wants_help:
        return None, wants_help
    return installed.get(DEFAULT_ADAPTER), wants_help


def _adapter_params(
    adapter_cls: type[HarlequinAdapter], reserved: set[str], taken: set[str]
) -> list[click.Parameter]:
    """One adapter's connection options, as click parameters.

    hsql's own flags are the part of it that is an API, so a declaration that
    collides with one loses the colliding spelling -- visibly, in
    `hsql --help -a <adapter>` -- rather than shadowing it.
    """

    def _stub() -> None: ...

    decorated: Any = _stub
    for option in adapter_cls.ADAPTER_OPTIONS or []:
        decorated = option.to_click()(decorated)

    params: list[click.Parameter] = []
    for param in reversed(getattr(decorated, "__click_params__", [])):
        param.opts = [opt for opt in param.opts if opt not in reserved]
        param.secondary_opts = [
            opt for opt in param.secondary_opts if opt not in reserved
        ]
        if param.opts and param.name not in taken:
            params.append(param)
    return params


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
        diagnostics.error(f"--{chosen[0]} and -F {named} are two output formats.")
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
    executed: Sequence[tuple[ExecutedStatement, float]], spec: str
) -> list[tuple[ExecutedStatement, float]] | None:
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


def _output_options(
    *,
    tuples_only: bool,
    no_align: bool,
    no_header: bool,
    null_string: str | None,
    color: bool,
    format_name: str,
) -> tuple[LayoutOptions, dict[str, Any]]:
    """psql's flag algebra, in the vocabulary each family of format speaks.

    `-t` is two independent switches rather than a mode, which is why `-tA`
    needs no special case: it never was one.
    """
    from harlequin.layout import LayoutOptions

    header = not (tuples_only or no_header)
    layout_options = LayoutOptions(
        header=header,
        footer=not tuples_only,
        aligned=not no_align,
        null_string=null_string,
        color=color,
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
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


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
    for all of them rather than for whichever is the default.
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
            "Exit codes:\n"
            "  0 success           1 query error       2 usage/config error\n"
            "  3 connection error  4 timeout           130 interrupted"
        ),
        adapters,
    ]
    # a lone \b tells click not to rewrap the paragraph that follows it
    return "\n\n".join(f"\b\n{block}" for block in blocks)
