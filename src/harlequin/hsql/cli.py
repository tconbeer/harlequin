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
itself, `--info` on the installation, and `--skill` writes the Agent Skill that
ships in the wheel, rather than any of them running SQL, so the first pass skips
the profile. None of them names an adapter either, and the command carries no
connection options at all, except for `--config init`: the
options it writes into a profile are the ones an adapter declares. `--catalog`
and `--catalog-search` are the modes that do connect, so they take a profile and an
adapter exactly as a run does. Modes are options rather than subcommands
because `CONN_STR` is positional: `hsql catalog` and a DuckDB file named
`catalog` would have needed a rule, and `--catalog` needs none. They are
mutually exclusive, and each lives in `harlequin.hsql.modes`, imported by the
callback when it is chosen.

A **session** is the fourth shape, and it is two roles of this one command.
`--serve NAME` connects and then holds the connection, answering invocations
sent to it over a socket; `--session NAME` is such an invocation, and it is the
client in `harlequin.hsql.client` that carries it there, forwarding argv
verbatim. So the server parses every served request with this same command,
and what tells the callback it is answering for a session is `ctx.obj`. Every
option here is in exactly one of three groups, decided by when its question
can be answered: connection-time (`CONN_STR`, `-a`, `-P`, the SSH options,
every adapter's), per-request (`-c`, `--format`, every mode), and
server-lifetime (`--queue-timeout`). `--serve` refuses the second group and a
served request refuses the third, and each refusal names the invocation the
option belongs on.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
    Callable,
    Iterator,
    Mapping,
    Sequence,
    TypeVar,
    cast,
)

import click

from harlequin.config import (
    CLI_ONLY_SESSION_KEYS,
    CLI_ONLY_SSH_KEYS,
    DEFAULT_ADAPTER,
    DEFAULT_SSH_TIMEOUT,
    SHARED_ONLY_KEYS,
    SSH_KEYS,
    TUI_ONLY_KEYS,
    UNLIMITED,
    merge_profile_with_cli,
    parse_profile_options,
    parse_row_count,
    parse_seconds,
    take_history_key,
    take_ssh_keys,
)
from harlequin.exception import (
    HarlequinCatalogPathError,
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinError,
    HarlequinSshError,
)
from harlequin.export import names_a_directory
from harlequin.first_pass import (
    attach_adapter_options,
    command_spellings,
    first_pass,
)
from harlequin.hsql import diagnostics, output
from harlequin.hsql.diagnostics import ExitCode
from harlequin.hsql.modes import CONFIG_MODES, INIT
from harlequin.plugins import adapter_names, load_adapter
from harlequin.redact import hide_secrets_in

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinAdapter, HarlequinConnection
    from harlequin.hsql.server import Served
    from harlequin.hsql.timeout import Deadline
    from harlequin.layout import LayoutOptions
    from harlequin.navigate import CatalogPath
    from harlequin.query import ExecutedStatement, OnError, ResultSet, RowLimit
    from harlequin.query_log import QueryLog, Status
    from harlequin.ssh import SshTunnel
    from harlequin.statements import Statement

T = TypeVar("T")

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

CONNECTION_OPTIONS = frozenset({"conn_str", "adapter", "read_only", *SSH_KEYS})
"""Answered once, when a connection is opened -- so `--serve` takes them and a
served request may not. Every adapter option is one too, by
`_is_connection_option()`: the command declares none of them itself."""

CONFIG_OPTIONS = frozenset({"profile", "config_path"})
"""Which file and which profile the other options are read from.

Not connection-time, though a profile usually holds connection-time keys:
these name where values come from rather than being values, so which group
one belongs to is decided by what it resolves to. A profile of nothing but
`format` and `limit` is per-request whichever way it was named, which is what
lets a served `-P` behave the way a discovered `default_profile` does.
"""

SERVER_OPTIONS = frozenset({"queue_timeout"})
"""Answered once, and only a server has one to answer."""

ROLE_OPTIONS = frozenset({"serve", "session"})
"""The two spellings that say which process an invocation is."""

PER_REQUEST_OPTIONS = frozenset(
    {
        "command",
        "file",
        "output",
        "format",
        *SHORTHANDS,
        "vertical",
        "tuples_only",
        "no_align",
        "no_header",
        "no_footer",
        "null_string",
        "timeout",
        "config_mode",
        "catalog",
        "catalog_search",
        "path",
        "spec",
        "info",
        "skill",
        "session_reset",
        "limit",
        "display_rows",
        "result",
        "on_error",
        "stats",
        "color",
        "version",
    }
)
"""Answered on every invocation, so a session's client sends them and
`--serve` takes none. `tests/unit_tests/test_hsql_serve.py` pins the four
groups to the command: every parameter is in exactly one."""


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
            click.Option(["--skill"], is_flag=True),
            click.Option(["--session-reset"], is_flag=True),
        ],
        # A mode reports on what is installed or configured rather than
        # connecting with it, so there is no profile to read for one. `--config`
        # reads the files itself and reports what it finds wrong with them under
        # its own exit code, rather than having the first pass hold an error
        # about the first file it stumbled on, and `--info` reports one as part
        # of its answer; `--spec` describes the command, which a config file it
        # could not read has no bearing on.
        # `--session-reset` asks a running session to reconnect with whatever
        # it connected with, so it takes no profile and no adapter either.
        needs_profile=lambda params: (
            not (
                params.get("config") is not None
                or params.get("spec")
                or params.get("info")
                or params.get("skill")
                or params.get("session_reset")
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
                or params.get("skill")
                or params.get("session_reset")
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
        metavar="PATH",
        help="Write results to PATH instead of stdout. Accepts a file or directory.",
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
    @click.option(
        "-x",
        "--vertical",
        is_flag=True,
        help="Shorthand for --format vertical. As in psql.",
    )
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
    @click.option(
        "-r",
        "--read-only",
        "read_only",
        is_flag=True,
        help=(
            "Connect read-only, and refuse to run at all if the adapter cannot. "
            "To check an adapter's capabilities, use --info."
        ),
    )
    @click.option(
        "--timeout",
        metavar="SECONDS",
        type=click.FloatRange(min=0, min_open=True),
        help=(
            "Cancel the run after SECONDS and exit 4. Refused if the adapter "
            "cannot cancel a query; to check, use --info."
        ),
    )
    @click.option(
        "--ssh-host",
        metavar="TEXT",
        help=(
            "Open an SSH tunnel to this destination first, and connect through "
            "it. A Host alias, host, user@host or ssh://user@host:port, passed "
            "to ssh verbatim."
        ),
    )
    @click.option(
        "--ssh-forward",
        metavar="TEXT",
        multiple=True,
        help=(
            "A local forward, spelled as ssh -L takes one: LOCAL:HOST:REMOTE. "
            "Repeatable. Omit it when your ssh config has the LocalForward."
        ),
    )
    @click.option(
        "--ssh-batch-mode",
        is_flag=True,
        help=(
            "Fail rather than prompt for a passphrase, a password or a host "
            "key. ssh's own BatchMode; set it in scripts, CI and cron."
        ),
    )
    @click.option(
        "--ssh-allow-reuse",
        is_flag=True,
        help=(
            "When the local port is already bound, warn and connect through "
            "the listener that has it instead of failing."
        ),
    )
    @click.option(
        "--ssh-timeout",
        metavar="SECONDS",
        type=click.FloatRange(min=0, min_open=True),
        help=(
            "Seconds to wait for the tunnel's forwards. "
            f"[default: {DEFAULT_SSH_TIMEOUT:g}]"
        ),
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
        help="Use this config file instead of the ones hsql discovers.",
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
        "--catalog",
        is_flag=True,
        help=(
            "List the catalog objects one level below --path, and exit without "
            "running SQL."
        ),
    )
    @click.option(
        "--catalog-search",
        metavar="TERM",
        help=(
            "Search the whole catalog, at every level, for objects whose "
            "name contains TERM, and exit without running SQL. Not every "
            "adapter can; see --info."
        ),
    )
    @click.option(
        "--path",
        metavar="TEXT",
        help=(
            "Where in the catalog --catalog looks, and what --catalog-search "
            "searches under. Dotted segments, named by the adapter; the top of "
            "the catalog by default. A trailing * filters a --catalog listing."
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
        "--skill",
        is_flag=True,
        help=(
            "Write the Agent Skill for driving hsql, as markdown. -o installs "
            "it: 'hsql --skill -o ~/.claude/skills/hsql/'."
        ),
    )
    @click.option(
        "--serve",
        metavar="NAME",
        help=(
            "Connect, then hold the connection open as the session named NAME "
            "and answer `--session NAME` invocations from it until stopped. "
            "Takes connection options only. Not on native Windows."
        ),
    )
    @click.option(
        "--session",
        metavar="NAME",
        help=(
            "Send this invocation to the running session named NAME, started "
            "with --serve. HSQL_SESSION=NAME does the same for every "
            "invocation, and runs without the session, with a warning, when "
            "none is up."
        ),
    )
    @click.option(
        "--session-reset",
        is_flag=True,
        help=(
            "Ask the session to close its connection and open a fresh one, "
            "and exit without running SQL. Temp tables, settings and an open "
            "transaction are gone. Needs --session."
        ),
    )
    @click.option(
        "--queue-timeout",
        metavar="SECONDS",
        type=click.FloatRange(min=0, min_open=True),
        help=(
            "With --serve: a request waits at most SECONDS for the one before "
            "it, then exits 4 without reaching the database. [default: no "
            "limit]"
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
        skill: bool,
        catalog: bool,
        catalog_search: str | None,
        path: str | None,
        serve: str | None,
        session: str | None,
        session_reset: bool,
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
        # the partition refusals reach past `kwargs`, because the mode flags and
        # the two role flags are named parameters of this callback rather than
        # keys of it -- so a `--catalog` or a `--serve` the caller typed would
        # otherwise be invisible to a check that only reads what the merge sees
        typed_options = {
            name
            for name in ctx.params
            if ctx.get_parameter_source(name) != click.core.ParameterSource.DEFAULT
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

        # the session this invocation is answered by, when a server built
        # this command to answer it
        served: Served | None = ctx.obj
        _refuse_session_keys_from_a_profile(ctx, values)
        # a mode that reads no database is not the session's business: it
        # runs where the caller is, with the options the caller typed
        connects = not (skill or info or spec or config_mode is not None)
        if serve is not None and (session is not None or served is not None):
            diagnostics.error(
                "--serve starts a session and --session sends to one; pass one of them."
            )
            ctx.exit(ExitCode.USAGE)
        if session is not None:
            # `main()` reads this flag before it parses anything, so it only
            # arrives here when something else built the command
            diagnostics.error(
                f"--session {session} is read before hsql parses anything else, "
                f"so it cannot be answered from here; run '{PROGRAM} --session "
                f"{session} ...' from a shell."
            )
            ctx.exit(ExitCode.USAGE)
        if serve is not None:
            _refuse_per_request_options(ctx, name=serve, typed=typed_options)
            _refuse_unservable_name(ctx, serve)
        elif served is not None:
            _refuse_the_sessions_options(
                ctx,
                served,
                typed=typed_options,
                connects=connects,
                profile=profile,
                profile_config=profile_config,
            )
        elif "queue_timeout" in explicitly_set:
            diagnostics.error(
                "--queue-timeout is a --serve option: it bounds how long a "
                "request waits for the one before it."
            )
            ctx.exit(ExitCode.USAGE)
        raw_queue_timeout = values.pop("queue_timeout", None)

        # redact secrets in config values and CLI args
        hide_secrets_in(
            values, adapter_cls.ADAPTER_OPTIONS if adapter_cls is not None else None
        )

        # every key hsql owns comes off here; whatever is left is the adapter's
        conn_str: Sequence[str] | str = values.pop("conn_str", tuple())
        if isinstance(conn_str, str):
            conn_str = (conn_str,)
        adapter: str = values.pop("adapter", DEFAULT_ADAPTER)
        destination = _Destination.parse(values.pop("output", None))
        result_spec: str = str(values.pop("result", "all"))
        raw_on_error = str(values.pop("on_error", "stop"))
        if raw_on_error not in ("stop", "continue"):
            # click's Choice already vetted the flag; a profile can say anything
            diagnostics.error(f"on_error takes stop or continue, not {raw_on_error}.")
            ctx.exit(ExitCode.USAGE)
        on_error: OnError = "continue" if raw_on_error == "continue" else "stop"
        read_only: bool = bool(values.pop("read_only", False))
        ssh_config: dict[str, Any] = {}
        try:
            # off the config either way: what is left of it is the adapter's
            record_history = take_history_key(values)
        except HarlequinConfigError as e:
            diagnostics.report_error(e)
            ctx.exit(ExitCode.USAGE)
        if served is not None and connects:
            # the session connected when it started, through whatever tunnel
            # it has, as whichever adapter it was given: a profile discovered
            # where the caller is does not change any of that
            for key in SSH_KEYS:
                values.pop(key, None)
            adapter = served.adapter
            read_only = False
        else:
            try:
                ssh_config = take_ssh_keys(values, typed=explicitly_set)
            except HarlequinConfigError as e:
                diagnostics.report_error(e)
                ctx.exit(ExitCode.USAGE)
        # a server runs no SQL of its own, so a --timeout a profile set for the
        # requests is not one the server counts down
        raw_timeout = values.pop("timeout", None)
        if serve is not None:
            raw_timeout = None
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

        mode = _one_mode(
            ctx,
            catalog=catalog,
            catalog_search=catalog_search,
            config_mode=config_mode,
            spec=spec,
            info=info,
            skill=skill,
            serve=serve,
            session_reset=session_reset,
        )
        if path is not None and not catalog and catalog_search is None:
            diagnostics.error("--path must be used with --catalog or --catalog-search.")
            ctx.exit(ExitCode.USAGE)
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

        if "tuples_only" in explicitly_set:
            # ahead of the modes as well as the connection: `-t nord` does not
            # reliably fail, so there may be no error for this to explain.
            diagnostics.report_theme_confusion(conn_str)

        if skill:
            ctx.exit(
                _report_skill(
                    ctx,
                    destination=destination,
                    format_name=format_name,
                    format_chosen=format_name != DEFAULT_FORMAT
                    or "format" in explicitly_set,
                )
            )

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
            # only `--config init` reaches this line
            _refuse_undeclared_read_only(
                ctx,
                adapter=adapter,
                asked=read_only,
                typed="read_only" in explicitly_set,
            )
            _refuse_undeclared_timeout(
                ctx,
                adapter=adapter,
                seconds=_timeout_seconds(ctx, raw_timeout),
                typed="timeout" in explicitly_set,
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

        # every mode below this connects to the database
        if session_reset:
            if served is None:
                diagnostics.error(
                    "--session-reset asks a running session, and this invocation "
                    "names none. Pass --session NAME, or set HSQL_SESSION."
                )
                ctx.exit(ExitCode.USAGE)
            ctx.exit(_reset_session(ctx, served))

        _refuse_undeclared_read_only(
            ctx,
            adapter=adapter,
            asked=read_only,
            typed="read_only" in explicitly_set,
        )
        timeout_seconds = _timeout_seconds(ctx, raw_timeout)
        _refuse_undeclared_timeout(
            ctx,
            adapter=adapter,
            seconds=timeout_seconds,
            typed="timeout" in explicitly_set,
        )
        deadline = _deadline(
            timeout_seconds, abandon=None if served is None else served.abandon
        )
        queue_timeout = _timeout_seconds(ctx, raw_queue_timeout, key="--queue-timeout")

        if ssh_config.get("ssh_host"):
            # entered before the child exists: `start()` blocks for the whole
            # handshake, and a signal caught in that window would otherwise
            # leave `ssh` running
            from harlequin.ssh import stopping_on_signal

            ctx.with_resource(stopping_on_signal())
        tunnel = _open_tunnel(ctx, ssh_config)
        if tunnel is not None:
            # the run's, not the process's: click closes the context however
            # this ends, and `harlequin.ssh` keeps an atexit backstop under that
            ctx.call_on_close(tunnel.stop)
            diagnostics.report_tunnel(tunnel.notice(), tunnel.warnings())

        if serve is not None:
            ctx.exit(
                _serve(
                    ctx,
                    serve,
                    adapter=adapter,
                    conn_str=conn_str,
                    read_only=read_only,
                    values=values,
                    queue_timeout=queue_timeout,
                )
            )

        if catalog:
            catalog_path = _catalog_path(ctx, path)
            if "limit" in explicitly_set:
                # ahead of the connection, so it is said whether or not the
                # database answers
                diagnostics.report_limit_ignored("--catalog")
            connection, _ = _connection_for(
                ctx,
                served,
                adapter=adapter,
                conn_str=conn_str,
                read_only=read_only,
                values=values,
                tunnel=tunnel,
            )
            ctx.exit(
                _under_deadline(
                    ctx,
                    lambda: _report_catalog(
                        ctx,
                        connection,
                        catalog_path,
                        destination=destination,
                        format_name=format_name,
                        display_rows=raw_display_rows,
                        tuples_only=tuples_only,
                        no_align=no_align,
                        no_header=no_header,
                        no_footer=no_footer,
                        null_string=null_string,
                        color=_use_color(color_when, destination),
                    ),
                    deadline=deadline,
                    connection=connection,
                )
            )

        if catalog_search is not None:
            if not catalog_search.strip():
                # an unset shell variable, far more often than a deliberate ask
                # for the whole catalog -- which is what --catalog is for, a
                # level at a time.
                diagnostics.error("--catalog-search needs a term to search for.")
                ctx.exit(ExitCode.USAGE)
            search_path = _search_path(ctx, path)
            _refuse_undeclared_search(ctx, adapter=adapter, term=catalog_search)
            if "limit" in explicitly_set:
                # ahead of the connection, so it is said whether or not the
                # database answers
                diagnostics.report_limit_ignored("--catalog-search")
            connection, _ = _connection_for(
                ctx,
                served,
                adapter=adapter,
                conn_str=conn_str,
                read_only=read_only,
                values=values,
                tunnel=tunnel,
            )
            ctx.exit(
                _under_deadline(
                    ctx,
                    lambda: _report_catalog_search(
                        ctx,
                        connection,
                        catalog_search,
                        search_path,
                        destination=destination,
                        format_name=format_name,
                        display_rows=raw_display_rows,
                        tuples_only=tuples_only,
                        no_align=no_align,
                        no_header=no_header,
                        no_footer=no_footer,
                        null_string=null_string,
                        color=_use_color(color_when, destination),
                    ),
                    deadline=deadline,
                    connection=connection,
                )
            )

        if not sources:
            diagnostics.error(
                "no SQL to run. Pass -c/--command or -f/--file, "
                f"or see '{PROGRAM} --help'."
            )
            ctx.exit(ExitCode.USAGE)

        if served is not None and served.stdin is None and _names_stdin(sources):
            # the client's argv scan is a scan, not a parse, and a boolean
            # short option an adapter declares is not in it: `-zf -` for such
            # a `-z` reaches here with no stdin, and must not run an empty
            # script as if that were what was piped
            diagnostics.error(
                "-f - reads standard input, and the request the session received "
                "carried none. Spell it --file -, which the session's client "
                "always reads."
            )
            ctx.exit(ExitCode.USAGE)

        try:
            statements = _read_statements(sources)
        except OSError as e:
            diagnostics.error(f"could not read {e.filename}: {e.strerror}")
            ctx.exit(ExitCode.USAGE)
        except HarlequinConfigError as e:
            diagnostics.report_error(e)
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

        connection, keyed_connection = _connection_for(
            ctx,
            served,
            adapter=adapter,
            conn_str=conn_str,
            read_only=read_only,
            values=values,
            tunnel=tunnel,
        )

        from harlequin.query_log import QueryLog

        query_log = QueryLog(
            program=PROGRAM,
            connection=keyed_connection,
            profile=profile,
            adapter=adapter,
            enabled=record_history,
        )
        # the run's, not the process's: click closes the context however this
        # ends, and every write is committed before then in any case
        ctx.call_on_close(query_log.close)

        run = _Run(deadline=deadline, log=query_log)
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

        def run_sql() -> None:
            """Everything the clock covers: execute, fetch, and write.

            One callable because the work `--timeout` bounds runs on a worker
            thread -- and all of it does, since a lazy adapter does the work in
            `fetchall()` rather than in `execute()`.
            """
            executed = _execute_all(
                connection, statements, limit=row_limit, on_error=on_error, run=run
            )
            if run.stopped:
                # a cancelled script produced fewer result sets than it was
                # going to, and `--result 2` on one of them is not the caller's
                # mistake to be told about
                return

            selected = _select_results(executed, result_spec)
            if selected is None:
                ctx.exit(ExitCode.USAGE)
            if (
                len(selected) > 1
                and not destination.is_directory
                and not output.holds_many(format_name)
            ):
                n = len(selected)
                diagnostics.error(
                    f"{n} result sets, but {format_name} holds one; "
                    f"use --result last, --result {n}, or -o DIR for one file each"
                )
                ctx.exit(ExitCode.USAGE)

            try:
                if (directory := destination.directory) is not None:
                    _emit_files(
                        selected,
                        directory,
                        limit=row_limit,
                        format_name=format_name,
                        layout_options=layout_options,
                        file_options=file_options,
                        on_error=on_error,
                        run=run,
                    )
                else:
                    with _sink(
                        destination, filename=f"results{output.suffix(format_name)}"
                    ) as out:
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

        if deadline is None:
            run_sql()
        else:
            # not `_under_deadline()`, which exits: this is the one path with
            # something left to write, since `--stats` reports the run that
            # ran out as well as the one that finished.
            from harlequin.hsql.timeout import TimedOut

            try:
                deadline.run(run_sql, connection=connection)
            except TimedOut:
                run.timed_out = deadline.seconds
                diagnostics.report_timeout(deadline.seconds)

        if query_log.failure is not None:
            # once, and after the run: a store that would not open never stops
            # a query, and this is the only way a caller learns their history
            # has a hole in it
            diagnostics.report_query_log_failure(query_log.failure)

        if stats:
            diagnostics.report_stats(
                status=run.status,
                statements=run.statements,
                rows=run.rows,
                truncated=run.truncated,
                limit=row_limit.max_rows,
                elapsed_ms=run.elapsed_ms,
                columns=run.columns,
                message=run.message,
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
                | set(TUI_ONLY_KEYS)
                | set(SHARED_ONLY_KEYS),
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
    ctx: click.Context,
    *,
    catalog: bool,
    catalog_search: str | None,
    config_mode: str | None,
    spec: bool,
    info: bool,
    skill: bool,
    serve: str | None = None,
    session_reset: bool = False,
) -> str | None:
    """The one mode this invocation chose, or exit having named the two it did.

    Modes are options rather than subcommands, so nothing about the parse stops
    a caller passing two of them -- and two questions in one invocation has no
    answer that is not a guess about which they meant.
    """
    asked = (
        ("--catalog", catalog),
        ("--catalog-search", catalog_search is not None),
        (f"--config {config_mode}", config_mode is not None),
        ("--spec", spec),
        ("--info", info),
        ("--skill", skill),
        (f"--serve {serve}", serve is not None),
        ("--session-reset", session_reset),
    )
    chosen = [name for name, was_asked in asked if was_asked]
    if len(chosen) > 1:
        diagnostics.error(
            f"{chosen[0]} and {chosen[1]} are two modes; pass one of them."
        )
        ctx.exit(ExitCode.USAGE)
    return chosen[0] if chosen else None


def _open_tunnel(
    ctx: click.Context, ssh_config: Mapping[str, Any]
) -> "SshTunnel | None":
    """The tunnel this run connects through, or exit having said why there is none.

    Ahead of the connection and of anything that writes to stdout, because this
    is where `ssh` may still ask a human for a passphrase. An unattended caller
    passes `--ssh-batch-mode` and gets the refusal immediately instead.
    """
    if not ssh_config.get("ssh_host"):
        # nothing to open, and so nothing to import
        return None
    from harlequin.ssh import open_tunnel

    try:
        return open_tunnel(ssh_config)
    except HarlequinConfigError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    except HarlequinSshError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.CONNECTION)


def _connect(
    ctx: click.Context,
    *,
    adapter: str,
    conn_str: Sequence[str],
    read_only: bool,
    values: Mapping[str, Any],
    tunnel: "SshTunnel | None" = None,
) -> tuple["HarlequinConnection", str]:
    """The connection this invocation runs on and the id it is logged under.

    Exits having said why not, if there is none.
    """
    adapter_instance = _adapter_instance(
        ctx, adapter=adapter, conn_str=conn_str, read_only=read_only, values=values
    )
    keyed = _keyed_connection(adapter_instance, conn_str, values, tunnel=tunnel)
    return _connected(ctx, adapter_instance), keyed


def _adapter_instance(
    ctx: click.Context,
    *,
    adapter: str,
    conn_str: Sequence[str],
    read_only: bool,
    values: Mapping[str, Any],
) -> "HarlequinAdapter":
    """The adapter this invocation connects with, or exit having said why not."""
    try:
        return load_adapter(adapter)(conn_str=conn_str, read_only=read_only, **values)
    except HarlequinConfigError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)


def _keyed_connection(
    adapter_instance: "HarlequinAdapter",
    conn_str: Sequence[str],
    values: Mapping[str, Any],
    *,
    tunnel: "SshTunnel | None",
) -> str:
    """The id one database is logged under.

    Derived here rather than beside each caller because the adapter instance it
    reads does not outlive the function that built it, and because both
    commands -- and a session's server -- have to key one database the same way.
    """
    from harlequin.query_log import connection_id

    return connection_id(
        adapter_instance.connection_id,
        conn_str,
        values,
        through=tunnel.cache_material() if tunnel is not None else (),
    )


def _connected(
    ctx: click.Context, adapter_instance: "HarlequinAdapter"
) -> "HarlequinConnection":
    try:
        return adapter_instance.connect()
    except HarlequinConnectionError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.CONNECTION)


def _connection_for(
    ctx: click.Context,
    served: "Served | None",
    *,
    adapter: str,
    conn_str: Sequence[str],
    read_only: bool,
    values: Mapping[str, Any],
    tunnel: "SshTunnel | None" = None,
) -> tuple["HarlequinConnection", str]:
    """The session's connection when there is a session, and a new one otherwise.

    Either way it comes back with the id it is logged under: the session
    derived its own when it connected, from the options a served request is
    refused, so a warm run and a cold one key one database alike.
    """
    if served is None:
        return _connect(
            ctx,
            adapter=adapter,
            conn_str=conn_str,
            read_only=read_only,
            values=values,
            tunnel=tunnel,
        )
    try:
        return served.connection(), served.connection_id
    except HarlequinConnectionError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.CONNECTION)


def _serve(
    ctx: click.Context,
    name: str,
    *,
    adapter: str,
    conn_str: Sequence[str],
    read_only: bool,
    values: Mapping[str, Any],
    queue_timeout: float | None,
) -> ExitCode:
    """Connect, and answer for the session called `name` until stopped.

    The server reconnects for `--session-reset`, so it is handed the adapter.
    """
    # here rather than at module scope: sockets and threads are the one
    # invocation in many that serves, and every other one would pay for them
    from harlequin.hsql.server import Server

    adapter_instance = _adapter_instance(
        ctx, adapter=adapter, conn_str=conn_str, read_only=read_only, values=values
    )
    # once, here: a served request is refused every option this is derived
    # from, so the answer cannot change between requests
    keyed = _keyed_connection(adapter_instance, conn_str, values, tunnel=None)
    connection = _connected(ctx, adapter_instance)
    return Server(
        name,
        adapter=adapter,
        connection=connection,
        connection_id=keyed,
        reconnect=adapter_instance.connect,
        queue_timeout=queue_timeout,
    ).serve()


def _reset_session(ctx: click.Context, served: "Served") -> ExitCode:
    """Answer `--session-reset` and return its code, or exit having said why not.

    Exit 3 for a reconnect that failed, since that is what the session now
    is: one with no connection, which the next request is told about too.
    """
    try:
        served.reset()
    except HarlequinConnectionError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.CONNECTION)
    diagnostics.note(f"session {served.name!r} reconnected.")
    return ExitCode.OK


def _is_connection_option(name: str) -> bool:
    """Whether an option is answered when a connection is opened.

    Every option is in exactly one group, and the command declares no
    adapter's options itself, so one it does not know is an adapter's -- and
    an adapter's options are all connection-time.
    """
    return name in CONNECTION_OPTIONS or name not in (
        PER_REQUEST_OPTIONS | SERVER_OPTIONS | ROLE_OPTIONS | CONFIG_OPTIONS
    )


def _spelling(ctx: click.Context, name: str) -> str:
    """How the command line spells a parameter, for an error that names it."""
    for param in ctx.command.params:
        if param.name == name:
            if isinstance(param, click.Argument):
                return param.human_readable_name
            return max(param.opts, key=len)
    return name


def _refuse_per_request_options(
    ctx: click.Context, *, name: str, typed: set[str]
) -> None:
    """Exit with an error if `--serve` was given an option a request answers.

    Every flag a server would need for a query is one it cannot answer once
    for every invocation to come, so the refusal names the invocation the
    option belongs on rather than only the one it does not.
    """
    for key in sorted(typed):
        if key in PER_REQUEST_OPTIONS:
            spelling = _spelling(ctx, key)
            diagnostics.error(
                f"{spelling} is a per-request option, and --serve takes none. "
                f"Start the session, then send it the request: "
                f"'{PROGRAM} --serve {name} ...', then "
                f"'{PROGRAM} --session {name} {spelling} ...'."
            )
            ctx.exit(ExitCode.USAGE)


def _refuse_unservable_name(ctx: click.Context, name: str) -> None:
    """Exit with an error if no client could ever reach a session so named.

    Asked of the name before a connection is paid for, and asked with the
    client's own check, so the two halves cannot disagree about a name.
    """
    from harlequin.hsql.client import cannot_be_served
    from harlequin.hsql.session import Session

    refusal = cannot_be_served(Session(name, explicit=True), os.environ)
    if refusal is not None:
        reason, remedy, code = refusal
        diagnostics.error(f"{reason}.{remedy}")
        ctx.exit(code)


def _refuse_the_sessions_options(
    ctx: click.Context,
    served: "Served",
    *,
    typed: set[str],
    connects: bool,
    profile: str | None,
    profile_config: Mapping[str, Any],
) -> None:
    """Exit with an error if a served request asked for what the session owns.

    A connection option describes a connection the session already opened,
    and a server-lifetime option describes a server that is up. A typed `-P`
    is judged by the keys its profile holds rather than by being one: a
    profile of per-request keys applies, and one that names a database is
    refused under the key that names it.

    Only a *typed* profile, because that is the caller asserting this profile
    for this invocation -- the rule `merge_profile_with_cli()` reads a command
    line by. One discovered in the caller's directory says nothing about the
    session, so its connection-time keys are moot rather than wrong.
    """
    for key in sorted(typed):
        spelling = _spelling(ctx, key)
        if key in SERVER_OPTIONS:
            diagnostics.error(
                f"{spelling} is a --serve option, and the session named "
                f"{served.name!r} already has one."
            )
            ctx.exit(ExitCode.USAGE)
        if connects and _is_connection_option(key):
            diagnostics.error(
                f"{spelling} is a connection option, and the session named "
                f"{served.name!r} connected when it started. Drop it, or start "
                f"a session with it: '{PROGRAM} --serve NAME {spelling} ...'."
            )
            ctx.exit(ExitCode.USAGE)
    if profile is None or "profile" not in typed:
        return
    for key in sorted(profile_config):
        if key in SERVER_OPTIONS or (connects and _is_connection_option(key)):
            diagnostics.error(
                f"the profile {profile!r} sets {key}, which the session named "
                f"{served.name!r} answered when it started. Use a profile of "
                f"per-request options here, or start a session with this one: "
                f"'{PROGRAM} --serve NAME -P {profile}'."
            )
            ctx.exit(ExitCode.USAGE)


def _refuse_session_keys_from_a_profile(
    ctx: click.Context, values: Mapping[str, Any]
) -> None:
    """Exit with an error if a config file said which process runs this.

    Only a profile can have put one of these in the merged values: the two
    flags are named parameters of the callback, so a typed one never reaches
    the merge.
    """
    for key in CLI_ONLY_SESSION_KEYS:
        if key in values:
            remedy = (
                f"Pass --{key}, or set HSQL_SESSION"
                if key == "session"
                else f"Pass --{key}"
            )
            diagnostics.error(
                f"{key} says which process runs an invocation, so it is read "
                f"from the command line and not from a config file. {remedy}."
            )
            ctx.exit(ExitCode.USAGE)


def _names_stdin(sources: Sequence[tuple[str, tuple[str, ...]]]) -> bool:
    """Whether any `-f` in the invocation reads standard input."""
    return any(kind == "file" and "-" in values for kind, values in sources)


def _catalog_path(ctx: click.Context, raw: str | None) -> "CatalogPath":
    """What `--path` named, or exit having said why it cannot be read.

    Called ahead of the connection, so a path this cannot read is refused
    without waiting on a database.
    """
    from harlequin.navigate import CatalogPath

    try:
        return CatalogPath.parse(raw)
    except HarlequinCatalogPathError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)


def _search_path(ctx: click.Context, raw: str | None) -> "CatalogPath":
    """What `--path` scopes a search to, or exit having said why it cannot.

    A trailing wildcard is refused rather than applied: `--catalog-search`
    already matches on a term, and a second, differently-spelled filter over
    the same names would only be a way to ask the same question twice.
    """
    path = _catalog_path(ctx, raw)
    if path.glob is not None:
        diagnostics.error(
            "--path cannot end in a wildcard with --catalog-search, which "
            "matches on TERM already."
        )
        ctx.exit(ExitCode.USAGE)
    return path


def _refuse_undeclared_search(ctx: click.Context, *, adapter: str, term: str) -> None:
    """Stop before connecting unless the adapter declares it can search.

    An adapter that cannot search is one whose catalog would have to be walked,
    and a `--catalog-search` that quietly walked it is the round-trip cliff
    this command refuses to have. Read off the class, so it costs the
    adapter's import and never a connection.
    """
    try:
        declares_search = load_adapter(adapter).IMPLEMENTS_CATALOG_SEARCH
    except HarlequinConfigError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    if not declares_search:
        diagnostics.error(
            f"{adapter} does not declare catalog search, so --catalog-search "
            f"{term!r} cannot be answered. List one level at a time with "
            f"{PROGRAM} --catalog, or see '{PROGRAM} --info'."
        )
        ctx.exit(ExitCode.USAGE)


def _refuse_undeclared_read_only(
    ctx: click.Context, *, adapter: str, asked: bool, typed: bool
) -> None:
    """Exit with an error if read-only was asked for and the adapter cannot."""
    if not asked:
        return
    try:
        declares_read_only = load_adapter(adapter).IMPLEMENTS_READ_ONLY
    except HarlequinConfigError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    if not declares_read_only:
        spelled = "--read-only" if typed else "read_only in the profile"
        diagnostics.error(
            f"{adapter} does not declare read-only support, so {spelled} "
            f"cannot be honored. See '{PROGRAM} --info'."
        )
        ctx.exit(ExitCode.USAGE)


def _refuse_undeclared_timeout(
    ctx: click.Context, *, adapter: str, seconds: float | None, typed: bool
) -> None:
    """Exit with an error if a deadline was set and the adapter cannot cancel.

    A clock that runs out over work nothing can stop leaves the query running
    and the caller told it stopped, which is worse than no flag at all.
    """
    if seconds is None:
        return
    try:
        declares_cancel = load_adapter(adapter).IMPLEMENTS_CANCEL
    except HarlequinConfigError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    if not declares_cancel:
        spelled = "--timeout" if typed else "timeout in the profile"
        diagnostics.error(
            f"{adapter} does not declare query cancellation, so {spelled} "
            f"cannot be honored. See '{PROGRAM} --info'."
        )
        ctx.exit(ExitCode.USAGE)


def _timeout_seconds(
    ctx: click.Context, raw: Any, *, key: str = "--timeout"
) -> float | None:
    """How long this invocation has, or exit having said why that is not a time.

    click has already vetted a `--timeout` typed on the command line; a profile
    can say anything.
    """
    try:
        return parse_seconds(raw, key=key)
    except HarlequinConfigError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)


def _deadline(
    seconds: float | None, *, abandon: Callable[[], None] | None = None
) -> "Deadline | None":
    """The clock this invocation runs under, or None for as long as it takes.

    Deferred, and None where nothing asked: a run with no deadline starts no
    worker thread, and the two phases stay on the thread they have always been
    on. `abandon` is a session's answer to a cancel that did not land, in
    place of ending the process.
    """
    if seconds is None:
        return None
    from harlequin.hsql.timeout import Deadline

    return Deadline(seconds, abandon=abandon)


def _under_deadline(
    ctx: click.Context,
    work: Callable[[], T],
    *,
    deadline: "Deadline | None",
    connection: "HarlequinConnection",
) -> T:
    """Run `work` under the clock, or exit 4 having said it ran out."""
    if deadline is None:
        return work()
    from harlequin.hsql.timeout import TimedOut

    try:
        return deadline.run(work, connection=connection)
    except TimedOut:
        diagnostics.report_timeout(deadline.seconds)
        ctx.exit(ExitCode.TIMEOUT)


def _report_catalog(
    ctx: click.Context,
    connection: "HarlequinConnection",
    path: "CatalogPath",
    *,
    destination: _Destination,
    format_name: str,
    display_rows: Any,
    **output_options: Any,
) -> ExitCode:
    """Write one level of the catalog and return its code, or exit saying why not."""
    # here rather than at module scope, for the reason each mode lives in its
    # own module: this one reaches the catalog walk and the row machinery.
    from harlequin.hsql.modes import catalog as catalog_mode

    try:
        display_limit = _display_limit(display_rows, format_name)
    except HarlequinConfigError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    layout_options, file_options = _output_options(
        format_name=format_name, display_limit=display_limit, **output_options
    )

    try:
        with _sink(destination, filename=f"catalog{output.suffix(format_name)}") as out:
            result = catalog_mode.report(
                out,
                connection=connection,
                path=path,
                format_name=format_name,
                layout_options=layout_options,
                file_options=file_options,
            )
            out.flush()
    except OSError as e:
        # a `-o PATH` it could not write, which is the caller's to fix
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    except Exception as e:  # noqa: BLE001 -- adapters are third-party code
        # a path that names nothing, or whatever the adapter raised fetching a
        # level: a code rather than a traceback for both.
        diagnostics.report_error(e)
        ctx.exit(diagnostics.exit_code_for(e))
    _report_hidden_rows(result, layout_options)
    return ExitCode.OK


def _report_catalog_search(
    ctx: click.Context,
    connection: "HarlequinConnection",
    term: str,
    path: "CatalogPath",
    *,
    destination: _Destination,
    format_name: str,
    display_rows: Any,
    **output_options: Any,
) -> ExitCode:
    """Write what the search found and return its code, or exit saying why not."""
    # here rather than at module scope, for the reason each mode lives in its
    # own module: this one reaches the catalog search and the row machinery.
    from harlequin.hsql.modes import catalog_search as catalog_search_mode

    try:
        display_limit = _display_limit(display_rows, format_name)
    except HarlequinConfigError as e:
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    layout_options, file_options = _output_options(
        format_name=format_name, display_limit=display_limit, **output_options
    )

    try:
        filename = f"catalog-search{output.suffix(format_name)}"
        with _sink(destination, filename=filename) as out:
            result = catalog_search_mode.report(
                out,
                connection=connection,
                term=term,
                path=path,
                format_name=format_name,
                layout_options=layout_options,
                file_options=file_options,
            )
            out.flush()
    except OSError as e:
        # a `-o PATH` it could not write, which is the caller's to fix
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    except Exception as e:  # noqa: BLE001 -- adapters are third-party code
        # a scope that names nothing, or whatever the adapter raised searching:
        # a code rather than a traceback for both.
        diagnostics.report_error(e)
        ctx.exit(diagnostics.exit_code_for(e))
    _report_hidden_rows(result, layout_options)
    return ExitCode.OK


def _report_info(
    ctx: click.Context,
    *,
    adapter: str | None,
    profile_name: str | None,
    config_path: Path | None,
    destination: _Destination,
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
        with _sink(destination, filename=info_mode.FILENAME) as out:
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


def _report_skill(
    ctx: click.Context,
    *,
    destination: _Destination,
    format_name: str,
    format_chosen: bool,
) -> ExitCode:
    """Answer `--skill` and return its code, or exit having said why not.

    stdout gets `SKILL.md` alone; a `-o` installs the skill, so the references
    go beside wherever the file landed. Both spellings of `-o` reach the same
    place -- `-o DIR/` names the directory and `-o DIR/SKILL.md` names the file
    in it -- because either is how a caller says "put the skill here".
    """
    # here rather than at module scope, for the reason the mode exists in its
    # own module -- though this is the one mode that would have cost nothing,
    # since it imports no adapter and reads no config
    from harlequin.hsql.modes import skill as skill_mode

    try:
        with _sink(destination, filename=skill_mode.FILENAME, announce=False) as out:
            code = skill_mode.report(
                out, format_name=format_name, format_chosen=format_chosen
            )
            out.flush()
        written = destination.file(skill_mode.FILENAME)
        if written is not None and format_name != skill_mode.NONE:
            diagnostics.report_written(
                [written, *skill_mode.install_references(written.parent)]
            )
    except OSError as e:
        # a `-o PATH` it could not write, which is the caller's to fix
        diagnostics.report_error(e)
        ctx.exit(ExitCode.USAGE)
    return code


def _report_spec(
    ctx: click.Context,
    *,
    adapter: str | None,
    destination: _Destination,
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
        with _sink(destination, filename=spec_mode.FILENAME) as out:
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
    destination: _Destination,
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
        with _sink(
            destination, filename=config_mode.filename(mode, format_name)
        ) as out:
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
    for key in CLI_ONLY_SSH_KEYS:
        # a profile that set one is refused on the next run, so writing it here
        # would be writing a file this command will not read back
        typed.pop(key, None)
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


def run(argv: Sequence[str], *, served: "Served | None" = None) -> int:
    """Parse and run one invocation, and return the code it exits with.

    The whole of the cold path, and the whole of one request on a session's
    server -- which is the point: a served invocation is this function with
    `served` set, and nothing else. What the callback needs to know about the
    session travels as `ctx.obj`.
    """
    try:
        # the same arguments to both: which adapter's options the command
        # carries is decided from them, and then click parses them.
        code = build_cli(argv).main(
            args=list(argv), prog_name=PROGRAM, standalone_mode=False, obj=served
        )
    except click.ClickException as e:
        # parse-level failures -- an unknown option, a bad choice. click already
        # exits 2 for those, which is the code hsql documents for usage errors.
        e.show()
        return e.exit_code
    except (click.Abort, KeyboardInterrupt):
        return ExitCode.INTERRUPT
    return code if isinstance(code, int) else ExitCode.OK


def bare_command() -> click.Command:
    """This command with no adapter's options on it, and no config file read.

    `harlequin` builds this to answer two questions about the other command:
    which spellings are hsql's, so it can point at them, and which profile keys
    hsql reads, so it does not mistake them for an adapter's options.
    """
    return build_cli(["-P", "None", "--help"])


@dataclass(frozen=True)
class _Destination:
    """Where `-o` said to write: a file, a folder to name files in, or stdout.

    A folder is what lets a format that holds one result set take a script that
    produced several -- one file each, named here rather than by the caller,
    which is why `diagnostics.report_written()` says what they were called.
    """

    path: Path | None = None
    is_directory: bool = False

    @classmethod
    def parse(cls, raw: str | Path | None) -> _Destination:
        """One `-o` value, as typed or as a profile wrote it."""
        if raw is None or raw == "":
            return cls()
        return cls(path=Path(raw).expanduser(), is_directory=names_a_directory(raw))

    @property
    def directory(self) -> Path | None:
        """The folder to write files into, or None for a file and for stdout."""
        return self.path if self.is_directory else None

    def file(self, filename: str) -> Path | None:
        """The one file to write, for a caller that writes exactly one.

        None is stdout. In a folder it is `filename`, which whatever is being
        written names -- a document has no position to be numbered by.
        """
        if self.path is None or not self.is_directory:
            return self.path
        return self.path / filename


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

    deadline: "Deadline | None" = None
    """The clock `--timeout` set, which the fetch loop reads between results."""

    timed_out: float | None = None
    """The deadline, in seconds, if it ran out on this run."""

    started: float = field(default_factory=time.monotonic)

    log: "QueryLog | None" = None
    """Where each statement is recorded, or None for a run that records none."""

    rows_written: dict[int, int | None] = field(default_factory=dict)
    """The query log's row for each statement, by its index in the script."""

    @property
    def elapsed_ms(self) -> int:
        return round((time.monotonic() - self.started) * 1000)

    def record(
        self,
        statement: Statement,
        *,
        status: Status = "ok",
        error: BaseException | None = None,
    ) -> None:
        """Log one statement, as the database is asked to run it.

        Before its rows are known, so that a run killed mid-fetch has still
        recorded the query; `record_result()` completes the row.
        """
        if self.log is None:
            return
        self.rows_written[statement.index] = self.log.write(
            statement.sql, status=status, error=_message_for(error)
        )

    def record_result(
        self,
        statement: Statement,
        *,
        status: Status = "ok",
        result: "ResultSet | None" = None,
        error: BaseException | None = None,
    ) -> None:
        """Complete one statement's row, now that its result is known."""
        if self.log is None:
            return
        self.log.update(
            self.rows_written.get(statement.index),
            status=status,
            rows=None if result is None else result.fetched_row_count,
            truncated=None if result is None else result.truncated,
            elapsed_ms=None if result is None else result.elapsed * 1000,
            error=_message_for(error),
        )

    @property
    def stopped(self) -> bool:
        """Whether the clock has run out, so nothing more is run or written.

        A cancelled query comes back empty and error-free, so a run that kept
        going would print the empty result the cancel produced as if the
        database had returned it.
        """
        return self.deadline is not None and self.deadline.expired

    @property
    def status(self) -> str:
        if self.failure is not None or self.timed_out is not None:
            return "error"
        return "ok"

    @property
    def message(self) -> str | None:
        """What went wrong, for `--stats`."""
        if self.timed_out is not None:
            return diagnostics.timeout_message(self.timed_out)
        return _message_for(self.failure)

    @property
    def exit_code(self) -> ExitCode:
        if self.timed_out is not None:
            return ExitCode.TIMEOUT
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
        if run.stopped:
            # not consuming the rest is what stops the script: a statement
            # submitted after the cancel would run past the deadline, and the
            # error the cancel itself raised is not one to report.
            break
        run.statements += 1
        if item.error is not None:
            run.failure = item.error
            run.record(item.statement, status="error", error=item.error)
            diagnostics.report_error(item.error)
        elif item.has_result_set:
            run.record(item.statement)
            executed.append(item)
        else:
            # DDL/DML: no cursor, so the row is complete where it is written
            run.record(item.statement)
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
    """Fetch each selected result set and write them all to one open stream."""
    for _, result in _fetched(selected, limit=limit, on_error=on_error, run=run):
        _write_result(
            result,
            out,
            limit=limit,
            format_name=format_name,
            layout_options=layout_options,
            file_options=file_options,
            run=run,
        )
    out.flush()


def _emit_files(
    selected: Sequence[ExecutedStatement],
    directory: Path,
    *,
    limit: RowLimit,
    format_name: str,
    layout_options: LayoutOptions,
    file_options: Mapping[str, Any],
    on_error: OnError,
    run: _Run,
) -> None:
    """Fetch each selected result set and write it to its own file in `directory`.

    Numbered as `--result` numbers them, so the second result set is
    `results_2` whether it was written beside four others or on its own.
    """
    if format_name == output.NONE:
        # the rows are discarded, so there is no file to name and no directory
        # to make -- but the fetch still runs, for --stats and for errors
        _emit(
            selected,
            io.BytesIO(),
            limit=limit,
            format_name=format_name,
            layout_options=layout_options,
            file_options=file_options,
            on_error=on_error,
            run=run,
        )
        return
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for position, result in _fetched(selected, limit=limit, on_error=on_error, run=run):
        path = directory / f"results_{position}{output.suffix(format_name)}"
        with path.open("wb") as f:
            _write_result(
                result,
                f,
                limit=limit,
                format_name=format_name,
                layout_options=layout_options,
                file_options=file_options,
                run=run,
            )
        written.append(path)
    diagnostics.report_written(written)


def _fetched(
    selected: Sequence[ExecutedStatement],
    *,
    limit: RowLimit,
    on_error: OnError,
    run: _Run,
) -> Iterator[tuple[int, ResultSet]]:
    """Each selected result set, fetched, with its position among them."""
    from harlequin.query import fetch

    for position, item in enumerate(selected, start=1):
        if run.stopped:
            # the clock ran out between statements
            run.record_result(item.statement, status="canceled")
            return
        try:
            result = fetch(item, limit=limit)
        except Exception as e:  # noqa: BLE001 -- adapters are third-party code
            if run.stopped:
                # whatever the cancel raised on the way out is not this run's
                # error to report; the deadline is
                run.record_result(item.statement, status="canceled")
                return
            run.failure = e
            run.record_result(item.statement, status="error", error=e)
            diagnostics.report_error(e)
            if on_error == "stop":
                return
            continue
        if run.stopped:
            # the cancel landed inside that fetch, so the rows it returned are
            # the ones it had rather than the ones the query has
            run.record_result(item.statement, status="canceled")
            return
        run.record_result(item.statement, result=result)
        yield position, result


def _write_result(
    result: ResultSet,
    out: BinaryIO,
    *,
    limit: RowLimit,
    format_name: str,
    layout_options: LayoutOptions,
    file_options: Mapping[str, Any],
    run: _Run,
) -> None:
    """Write one fetched result set, and record what it was."""
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


def _read_source_text(value: str) -> str:
    """The text of one `-f` source: a path, or `-` for stdin.

    A source that isn't text decodes as far as its first bad byte and then
    raises, which is a crash rather than a diagnostic unless it is caught here.

    Raises: HarlequinConfigError if the source is not UTF-8 text, OSError if
    the file cannot be read at all.
    """
    source: str
    read: Callable[[], str]
    if value == "-":
        # `-` is stdin, decoded the way click decodes it, so a stream
        # the environment has misconfigured still reads as text
        source, read = "standard input", click.open_file("-", mode="r").read
    else:
        path = Path(value).expanduser()
        source, read = str(path), partial(path.read_text, encoding="utf-8")
    try:
        return read()
    except UnicodeDecodeError as e:
        raise HarlequinConfigError(
            f"could not read {source}: not UTF-8 text ({e.reason} at byte {e.start}).",
            title="Harlequin could not read a SQL file.",
        ) from e


def _read_statements(
    sources: Sequence[tuple[str, tuple[str, ...]]],
) -> list[Statement]:
    """Every statement the invocation asked for, in order.

    Each source is split on its own, so two `-c` values are two statements
    whether or not either ends in a semicolon.

    Raises: HarlequinConfigError if a file is not text, OSError if it cannot
    be read at all.
    """
    from harlequin.statements import Statement, split

    statements: list[Statement] = []
    for kind, values in sources:
        for value in values:
            text = value if kind == "command" else _read_source_text(value)
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


def _use_color(when: str, destination: _Destination) -> bool:
    """Whether to style text output.

    The default is `never`, so the bytes do not depend on what stdout happens
    to be. `auto` opts into that dependence, and honors NO_COLOR; `always` is
    the caller saying they meant it. A file never gets escapes either way.
    """
    if destination.path is not None or when == "never":
        return False
    if when == "always":
        return True
    return sys.stdout.isatty() and not os.getenv("NO_COLOR")


@contextlib.contextmanager
def _sink(
    destination: _Destination, *, filename: str, announce: bool = True
) -> Iterator[BinaryIO]:
    """Where one document or one run of result sets goes: a path, or stdout.

    Binary in both cases. duckdb writes `\\n` and the layouts write `\\n`, and
    text-mode translation would turn that into `\\r\\n` on Windows for the same
    query that produced `\\n` everywhere else.

    The directory the file is in is created if it is not there, so that a path
    a caller has not made yet is a path they can write to.

    `announce=False` is for a caller that writes more than this one file, and
    so has to name them all in one line rather than this one on its own.
    """
    path = destination.file(filename)
    if path is None:
        # `-` is stdout, wrapped so that closing the stream is not on the table
        yield cast(BinaryIO, click.open_file("-", mode="wb"))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        yield f
    if destination.is_directory and announce:
        diagnostics.report_written([path])


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
            "Catalog:\n"
            f"  {PROGRAM} --catalog                     the top of the catalog\n"
            f"  {PROGRAM} --catalog --path db.schema    one level below that\n"
            f"  {PROGRAM} --catalog --path db.sch.tbl   a relation's columns\n"
            f"  {PROGRAM} --catalog-search orders       anything in it named that"
        ),
        (
            "Sessions (not on native Windows):\n"
            f"  {PROGRAM} --serve NAME [CONN_STR]      hold a connection open\n"
            f"  {PROGRAM} --session NAME -c ...        send it a query; or "
            "HSQL_SESSION=NAME\n"
            f"  {PROGRAM} --session NAME --session-reset   reconnect it"
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
