"""Every option the two commands declare, in one place.

A declaration says which command takes an option, how it spells it, and the
flags for where its value may be used. `docs/cli-options.md` is the reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypedDict

import click

from harlequin.config import DEFAULT_ADAPTER, DEFAULT_SSH_TIMEOUT, UNLIMITED

HARLEQUIN = "harlequin"
HSQL = "hsql"
"""The two commands, as a declaration names them."""

DEFAULT_THEME = "harlequin"
DEFAULT_KEYMAP_NAMES = ["vscode"]
DEFAULT_VIEWER_MAX_ROWS = 100_000

DEFAULT_FORMAT = "table"

DEFAULT_LIMIT = 500
"""Small on purpose: fetching a million rows to print forty is waste.

The *hard* limit -- `cursor.set_limit()` -- so `0` fetches a header alone.
"""

DEFAULT_IDLE_TIMEOUT = 1800.0
DEFAULT_MAX_LIFETIME = 28800.0
"""Idle and total bounds on a session, which `0` lifts."""


class Group(Enum):
    """When hsql reads an option's value, and so where it may be used:
    `--serve` refuses the per-request group, a served request the server's."""

    CONNECTION = "connection"
    """Read at connect time, so a session records it."""

    PER_REQUEST = "per-request"
    """Read per invocation, so a client sends it."""

    SERVER = "server"
    """Read at start-up, and bound to a server's lifetime."""

    ROLE = "role"
    """Read to decide which process runs the invocation."""

    CONFIG = "config"
    """Read to pick where the other values come from."""


@dataclass(frozen=True)
class RuntimeValue:
    """A stand-in for a value that only exists once a command is being built.

    It defers a choice of what is installed, a callback, or help naming a
    computed default: whatever holds it is resolved against the mapping the
    command passes to `attach_core_options()`, under `key`.
    """

    key: str


class ClickKwargs(TypedDict, total=False):
    """The click keywords a declaration may set, so a typo is an error.

    Each may hold a `RuntimeValue` in place of its own type.
    """

    help: str | RuntimeValue
    type: click.ParamType | RuntimeValue
    default: Any
    show_default: bool | str
    metavar: str
    is_flag: bool
    multiple: bool
    nargs: int
    expose_value: bool
    envvar: str
    show_envvar: bool
    callback: Callable[..., Any] | RuntimeValue


@dataclass(frozen=True)
class On:
    """A sentinel enabling an option for one command, and its overrides.

    `decls` *replaces* the shared spellings rather than adding to them;
    `kwargs` merge over the shared keywords, key by key. Empty where the
    command takes the option exactly as declared.
    """

    decls: tuple[str, ...] = ()
    kwargs: ClickKwargs = field(default_factory=ClickKwargs)


@dataclass(frozen=True)
class CoreOption:
    """One option of the `harlequin` or `hsql` command, and how it may be
    used. `name` is the click parameter, and the profile key."""

    name: str
    decls: tuple[str, ...] = ()
    group: Group | None = None
    """When hsql reads it; None of what hsql does not declare."""

    kwargs: ClickKwargs = field(default_factory=ClickKwargs)
    harlequin: On | None = None
    hsql: On | None = None
    cli_only: bool = False
    """Whether a config file is refused it: a value deciding which process
    runs, or one a discovered file may not weaken."""

    argument: bool = False
    """Whether it is positional. `CONN_STR` alone is."""

    first_pass: bool = False
    """Whether the pass that names the adapter reads it off raw argv."""

    decorator: RuntimeValue | None = None
    """A whole click decorator from the command, in place of building one.
    `--version` alone: click's `version_option()` writes the callback that
    prints the message, which is the command's own."""

    def __post_init__(self) -> None:
        if self.harlequin is None and self.hsql is None:
            raise ValueError(f"{self.name} is declared by neither command.")
        if (self.hsql is None) is not (self.group is None):
            raise ValueError(
                f"{self.name} must declare a group if and only if hsql takes it."
            )

    def config_for_command(self, command: str) -> On | None:
        """How `command` declares it, or None if it does not."""
        if command == HARLEQUIN:
            return self.harlequin
        if command == HSQL:
            return self.hsql
        raise ValueError(f"{command!r} is neither command.")

    def to_click(
        self,
        command: str,
        runtime_values: Mapping[str, Any],
        *,
        option_cls: type[click.Option] | None = None,
        argument_cls: type[click.Argument] | None = None,
    ) -> Callable[[click.Command], click.Command] | None:
        """The decorator that puts this option on `command`, or None.

        Click's `@option` appends to `Command.params`, which is what orders it.
        """
        command_config = self.config_for_command(command)
        if command_config is None:
            return None
        if self.decorator is not None:
            built: Callable[[click.Command], click.Command] = _unwrap_runtime_value(
                self.decorator, runtime_values, name=self.name
            )
            return built
        decls = command_config.decls or self.decls
        kwargs = _build_click_kwargs(
            {**self.kwargs, **command_config.kwargs}, runtime_values, name=self.name
        )
        if self.argument:
            if argument_cls is not None:
                kwargs["cls"] = argument_cls
            return click.argument(*decls, **kwargs)
        if option_cls is not None:
            kwargs["cls"] = option_cls
        return click.option(*decls, **kwargs)


def _build_click_kwargs(
    kwargs: Mapping[str, Any], runtime_values: Mapping[str, Any], *, name: str
) -> dict[str, Any]:
    """The declared keywords, with every `RuntimeValue` in them unwrapped."""
    return {
        key: _unwrap_runtime_value(value, runtime_values, name=name)
        if isinstance(value, RuntimeValue)
        else value
        for key, value in kwargs.items()
    }


def _unwrap_runtime_value(
    value: RuntimeValue, runtime_values: Mapping[str, Any], *, name: str
) -> Any:
    """What the command passed under this key, or a refusal naming it."""
    if value.key not in runtime_values:
        raise KeyError(f"{name} needs {value.key!r} from the command.")
    return runtime_values[value.key]


CORE_OPTIONS: Sequence[CoreOption] = (
    CoreOption(
        name="version",
        group=Group.PER_REQUEST,
        decorator=RuntimeValue("version_option"),
        harlequin=On(),
        hsql=On(),
    ),
    CoreOption(
        name="conn_str",
        decls=("conn_str",),
        group=Group.CONNECTION,
        kwargs={"nargs": -1},
        argument=True,
        harlequin=On(),
        hsql=On(),
    ),
    # --- the SQL to run, and where its results go ----------------------------
    CoreOption(
        name="command",
        decls=("-c", "--command"),
        group=Group.PER_REQUEST,
        kwargs={
            "multiple": True,
            "callback": RuntimeValue("record_source"),
            "help": "Execute SQL. Repeatable.",
        },
        hsql=On(),
    ),
    CoreOption(
        name="file",
        decls=("-f", "--file"),
        group=Group.PER_REQUEST,
        kwargs={
            "multiple": True,
            "callback": RuntimeValue("record_source"),
            "metavar": "PATH",
            "help": "Execute SQL from a file, or from stdin for `-`. Repeatable.",
        },
        hsql=On(),
    ),
    CoreOption(
        name="output",
        decls=("-o", "--output"),
        group=Group.PER_REQUEST,
        kwargs={
            "type": click.Path(file_okay=True, dir_okay=True, path_type=Path),
            "metavar": "PATH",
        },
        harlequin=On(
            kwargs={"help": "The default directory or file path for the Data Exporter."}
        ),
        hsql=On(
            kwargs={
                "help": (
                    "Write results to PATH instead of stdout. Accepts a file "
                    "or directory."
                )
            }
        ),
    ),
    # long spelling only: -F is psql's --field-separator, and a flag that sets
    # a delimiter in one command and picks a format in the other is a mistake
    # waiting for a script. The shorthand flags below cover the common choices.
    CoreOption(
        name="format",
        decls=("--format",),
        group=Group.PER_REQUEST,
        kwargs={
            "default": DEFAULT_FORMAT,
            "show_default": True,
            "metavar": "NAME",
            "type": RuntimeValue("formats"),
            "help": "Output format. See below for the list.",
        },
        hsql=On(),
    ),
    CoreOption(
        name="csv",
        decls=("--csv",),
        group=Group.PER_REQUEST,
        kwargs={"is_flag": True, "help": "Shorthand for --format csv."},
        hsql=On(),
    ),
    CoreOption(
        name="json",
        decls=("--json",),
        group=Group.PER_REQUEST,
        kwargs={"is_flag": True, "help": "Shorthand for --format json."},
        hsql=On(),
    ),
    CoreOption(
        name="jsonl",
        decls=("--jsonl",),
        group=Group.PER_REQUEST,
        kwargs={"is_flag": True, "help": "Shorthand for --format jsonl."},
        hsql=On(),
    ),
    CoreOption(
        name="markdown",
        decls=("--markdown",),
        group=Group.PER_REQUEST,
        kwargs={"is_flag": True, "help": "Shorthand for --format markdown."},
        hsql=On(),
    ),
    CoreOption(
        name="vertical",
        decls=("-x", "--vertical"),
        group=Group.PER_REQUEST,
        kwargs={
            "is_flag": True,
            "help": "Shorthand for --format vertical. As in psql.",
        },
        hsql=On(),
    ),
    CoreOption(
        name="tuples_only",
        decls=("-t", "--tuples-only"),
        group=Group.PER_REQUEST,
        kwargs={
            "is_flag": True,
            "help": "Rows only: no header, no footer. As in psql.",
        },
        hsql=On(),
    ),
    CoreOption(
        name="no_align",
        decls=("-A", "--no-align"),
        group=Group.PER_REQUEST,
        kwargs={"is_flag": True, "help": "Unaligned output. As in psql."},
        hsql=On(),
    ),
    CoreOption(
        name="no_header",
        decls=("--no-header",),
        group=Group.PER_REQUEST,
        kwargs={
            "is_flag": True,
            "help": "Omit the header row, keeping other chrome.",
        },
        hsql=On(),
    ),
    CoreOption(
        name="no_footer",
        decls=("--no-footer",),
        group=Group.PER_REQUEST,
        kwargs={
            "is_flag": True,
            "help": "Omit the row-count footer, keeping other chrome.",
        },
        hsql=On(),
    ),
    CoreOption(
        name="null_string",
        decls=("--null-string",),
        group=Group.PER_REQUEST,
        kwargs={
            "metavar": "TEXT",
            "help": (
                "Render NULL as TEXT. Defaults to NULL for text formats, empty for csv."
            ),
        },
        hsql=On(),
    ),
    # --- which file, and which profile, the rest are read from ---------------
    CoreOption(
        name="profile",
        decls=("-P", "--profile"),
        group=Group.CONFIG,
        first_pass=True,
        kwargs={
            "help": (
                "Select a profile from an available config file to load its "
                "values. Other options passed here will take precedence over "
                "those loaded from the profile. Use the special profile named "
                "None to use Harlequin's defaults, instead of the default "
                "profile specified in the config file."
            )
        },
        harlequin=On(),
        hsql=On(),
    ),
    # --- the connection ------------------------------------------------------
    CoreOption(
        name="adapter",
        group=Group.CONNECTION,
        first_pass=True,
        decls=("-a", "--adapter"),
        kwargs={
            "default": DEFAULT_ADAPTER,
            "show_default": True,
            "metavar": "NAME",
            "type": RuntimeValue("adapters"),
            "help": (
                "The name of an installed database adapter plug-in "
                "to use to connect to the database at CONN_STR."
            ),
        },
        harlequin=On(),
        hsql=On(),
    ),
    CoreOption(
        name="read_only",
        group=Group.CONNECTION,
        kwargs={"is_flag": True},
        decls=("-r", "--read-only", "read_only"),
        harlequin=On(
            kwargs={
                "help": (
                    "Connect read-only, and refuse to start at all if the adapter "
                    "cannot. To check an adapter's capabilities, use `hsql --info`."
                )
            }
        ),
        hsql=On(
            kwargs={
                "help": (
                    "Connect read-only, and refuse to run at all if the adapter "
                    "cannot. To check an adapter's capabilities, use --info."
                )
            }
        ),
    ),
    CoreOption(
        name="timeout",
        decls=("--timeout",),
        group=Group.PER_REQUEST,
        kwargs={
            "metavar": "SECONDS",
            "type": click.FloatRange(min=0, min_open=True),
            "help": (
                "Cancel the run after SECONDS and exit 4. Refused if the adapter "
                "cannot cancel a query; to check, use --info."
            ),
        },
        hsql=On(),
    ),
    # --- the tunnel ----------------------------------------------------------
    CoreOption(
        name="ssh_host",
        decls=("--ssh-host",),
        group=Group.CONNECTION,
        kwargs={
            "help": (
                "Open an SSH tunnel to this destination first, and connect through "
                "it. A Host alias, host, user@host or ssh://user@host:port, passed "
                "to ssh verbatim."
            ),
            "metavar": "TEXT",
        },
        harlequin=On(),
        hsql=On(),
    ),
    CoreOption(
        name="ssh_forward",
        decls=("--ssh-forward",),
        group=Group.CONNECTION,
        kwargs={
            "multiple": True,
            "metavar": "TEXT",
            "help": (
                "A local forward, spelled as ssh -L takes one: "
                "LOCAL:HOST:REMOTE. Repeat this option for more than one. "
                "Omit it when your ssh config already has the LocalForward."
            ),
        },
        harlequin=On(),
        hsql=On(),
    ),
    CoreOption(
        name="ssh_batch_mode",
        decls=("--ssh-batch-mode",),
        group=Group.CONNECTION,
        kwargs={
            "is_flag": True,
            "help": (
                "Fail rather than prompt for a passphrase, a password or a "
                "host key. ssh's own BatchMode."
            ),
        },
        harlequin=On(),
        hsql=On(),
    ),
    CoreOption(
        name="ssh_allow_reuse",
        decls=("--ssh-allow-reuse",),
        group=Group.CONNECTION,
        cli_only=True,
        kwargs={
            "is_flag": True,
            "help": (
                "When the local port is already bound, warn and connect through "
                "the listener that has it instead of failing."
            ),
        },
        harlequin=On(),
        hsql=On(),
    ),
    CoreOption(
        name="ssh_timeout",
        decls=("--ssh-timeout",),
        group=Group.CONNECTION,
        kwargs={
            "type": click.FloatRange(min=0, min_open=True),
            "metavar": "SECONDS",
            "help": (
                "Seconds to wait for the tunnel's forwards. Default is "
                f"{DEFAULT_SSH_TIMEOUT:g}."
            ),
        },
        harlequin=On(),
        hsql=On(),
    ),
    # existence is not click's to check for hsql: every mode that reads this
    # path already refuses a file that is not there, naming it, and `--config
    # init` is the one invocation whose whole job is to write a file that is
    # not there yet.
    CoreOption(
        name="config_path",
        decls=("--config-path",),
        group=Group.CONFIG,
        first_pass=True,
        kwargs={
            "envvar": "HARLEQUIN_CONFIG_PATH",
            "show_envvar": True,
            "metavar": "PATH",
            "help": (
                "By default, Harlequin finds files named .harlequin.toml in "
                "the current directory and the home directory (~) and merges "
                "them. Use this option to specify the full path to a config "
                "file at a different location."
            ),
        },
        harlequin=On(
            kwargs={
                "type": click.Path(
                    exists=True,
                    file_okay=True,
                    dir_okay=False,
                    resolve_path=True,
                    path_type=Path,
                )
            }
        ),
        hsql=On(
            kwargs={
                "type": click.Path(dir_okay=False, resolve_path=True, path_type=Path)
            }
        ),
    ),
    # --- the modes: report rather than run SQL -------------------------------
    CoreOption(
        name="config_mode",
        decls=("--config", "config_mode"),
        group=Group.PER_REQUEST,
        first_pass=True,
        kwargs={
            "metavar": "MODE",
            "type": RuntimeValue("config_modes"),
            "help": RuntimeValue("config_mode_help"),
        },
        hsql=On(),
    ),
    CoreOption(
        name="catalog",
        decls=("--catalog",),
        group=Group.PER_REQUEST,
        kwargs={
            "is_flag": True,
            "help": (
                "List the catalog objects one level below --path, and exit without "
                "running SQL."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="catalog_search",
        decls=("--catalog-search",),
        group=Group.PER_REQUEST,
        kwargs={
            "metavar": "TERM",
            "help": (
                "Search the whole catalog, at every level, for objects whose "
                "name contains TERM, and exit without running SQL. Not every "
                "adapter can; see --info."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="path",
        decls=("--path",),
        group=Group.PER_REQUEST,
        kwargs={
            "metavar": "TEXT",
            "help": (
                "Where in the catalog --catalog looks, and what --catalog-search "
                "searches under. Dotted segments, named by the adapter; the top of "
                "the catalog by default. A trailing * filters a --catalog listing."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="history",
        decls=("--history",),
        group=Group.PER_REQUEST,
        kwargs={
            "is_flag": True,
            "help": (
                "List the queries harlequin and hsql have run, newest first, and "
                "exit without running SQL. --limit says how many; -P, -a or a "
                "CONN_STR narrows it to one database."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="history_search",
        decls=("--history-search",),
        group=Group.PER_REQUEST,
        kwargs={
            "metavar": "TERM",
            "help": (
                "List the logged queries whose SQL contains TERM, newest first, "
                "and exit without running SQL. Scoped like --history."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="spec",
        decls=("--spec",),
        group=Group.PER_REQUEST,
        first_pass=True,
        kwargs={
            "is_flag": True,
            "help": (
                "Every option here, plus every installed adapter's, as JSON. "
                "-a narrows it to one adapter."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="info",
        decls=("--info",),
        group=Group.PER_REQUEST,
        first_pass=True,
        kwargs={
            "is_flag": True,
            "help": (
                "Versions, config files, the active profile, and what each "
                "installed adapter declares it supports, as JSON. Connects to "
                "nothing. -a narrows it to one adapter."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="skill",
        decls=("--skill",),
        group=Group.PER_REQUEST,
        first_pass=True,
        kwargs={
            "is_flag": True,
            "help": (
                "Write the Agent Skill for driving hsql, as markdown. -o installs "
                "it: 'hsql --skill -o ~/.claude/skills/hsql/'."
            ),
        },
        hsql=On(),
    ),
    # --- the two roles, and the session they share ---------------------------
    CoreOption(
        name="serve",
        decls=("--serve",),
        group=Group.ROLE,
        cli_only=True,
        kwargs={
            "metavar": "NAME",
            "help": (
                "Connect, then hold the connection open as the session named NAME "
                "and answer `--session NAME` invocations from it until stopped. "
                "Takes connection and session-lifetime options; no per-request "
                "ones. Not on native Windows."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="session",
        decls=("--session",),
        group=Group.ROLE,
        cli_only=True,
        kwargs={
            "metavar": "NAME",
            "help": (
                "Send this invocation to the running session named NAME, started "
                "with --serve. HSQL_SESSION=NAME does the same for every "
                "invocation, and runs without the session, with a warning, when "
                "none is up."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="session_reset",
        decls=("--session-reset",),
        group=Group.PER_REQUEST,
        first_pass=True,
        kwargs={
            "is_flag": True,
            "help": (
                "Ask the session to close its connection and open a fresh one, "
                "and exit without running SQL. Temp tables, settings and an open "
                "transaction are gone. Needs --session."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="session_status",
        decls=("--session-status",),
        group=Group.PER_REQUEST,
        first_pass=True,
        cli_only=True,
        kwargs={
            "is_flag": True,
            "help": (
                "Poll the server for its status as JSON, and exit. Reports while "
                "a query is running. Needs --session."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="queue_timeout",
        decls=("--queue-timeout",),
        group=Group.SERVER,
        kwargs={
            "metavar": "SECONDS",
            "type": click.FloatRange(min=0, min_open=True),
            "help": (
                "With --serve: a request waits at most SECONDS for the one before "
                "it, then exits 4 without reaching the database. [default: no "
                "limit]"
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="idle_timeout",
        decls=("--idle-timeout",),
        group=Group.SERVER,
        kwargs={
            "metavar": "SECONDS",
            "default": DEFAULT_IDLE_TIMEOUT,
            "show_default": "1800 (30 minutes)",
            "type": click.FloatRange(min=0),
            "help": (
                "With --serve: stop the session once it has gone SECONDS with no "
                "request. 0 for a session that waits as long as it takes."
            ),
        },
        hsql=On(),
    ),
    CoreOption(
        name="max_lifetime",
        decls=("--max-lifetime",),
        group=Group.SERVER,
        kwargs={
            "metavar": "SECONDS",
            "default": DEFAULT_MAX_LIFETIME,
            "show_default": "28800 (8 hours)",
            "type": click.FloatRange(min=0),
            "help": (
                "With --serve: stop the session SECONDS after it connected, "
                "whatever it is doing; a request already running finishes first. "
                "0 for a session that runs until something stops it."
            ),
        },
        hsql=On(),
    ),
    # --- how many rows, and what to do about a failure -----------------------
    CoreOption(
        name="limit",
        decls=("--limit",),
        group=Group.PER_REQUEST,
        kwargs={"type": click.IntRange(min=UNLIMITED)},
        harlequin=On(
            kwargs={
                "help": (
                    "Default value for the limit control; if set, the limit will "
                    "be applied by default. If unset, queries fetch all rows."
                )
            }
        ),
        hsql=On(
            kwargs={
                "default": DEFAULT_LIMIT,
                "show_default": True,
                "metavar": "N",
                "help": "Maximum rows fetched per result set. -1 for no limit.",
            }
        ),
    ),
    CoreOption(
        name="display_rows",
        decls=("--display-rows",),
        group=Group.PER_REQUEST,
        kwargs={
            "metavar": "N",
            "type": click.IntRange(min=UNLIMITED),
            "help": RuntimeValue("display_rows_help"),
        },
        hsql=On(),
    ),
    CoreOption(
        name="result",
        decls=("--result",),
        group=Group.PER_REQUEST,
        kwargs={
            "default": "all",
            "show_default": True,
            "metavar": "all|last|N",
            "help": "Which result set(s) to emit.",
        },
        hsql=On(),
    ),
    CoreOption(
        name="on_error",
        decls=("--on-error",),
        group=Group.PER_REQUEST,
        kwargs={
            "default": "stop",
            "show_default": True,
            "type": click.Choice(["stop", "continue"]),
            "help": "What to do when a statement fails.",
        },
        hsql=On(),
    ),
    CoreOption(
        name="no_write_history",
        group=Group.PER_REQUEST,
        kwargs={"is_flag": True},
        harlequin=On(
            decls=("--no-write-history", "no_write_history"),
            kwargs={
                "help": (
                    "Do not record this session's queries in the query history "
                    "that Harlequin and hsql share."
                )
            },
        ),
        hsql=On(
            decls=("--no-write-history", "no_write_history"),
            kwargs={
                "help": (
                    "Do not record this run's queries in the query history that "
                    "Harlequin and hsql share."
                )
            },
        ),
    ),
    CoreOption(
        name="stats",
        decls=("--stats",),
        group=Group.PER_REQUEST,
        kwargs={"is_flag": True, "help": "Write a one-line JSON summary to stderr."},
        hsql=On(),
    ),
    CoreOption(
        name="color",
        decls=("--color",),
        group=Group.PER_REQUEST,
        kwargs={
            "default": "never",
            "show_default": True,
            "type": click.Choice(["auto", "always", "never"]),
            "help": "Color text output. `auto` follows the terminal and NO_COLOR.",
        },
        hsql=On(),
    ),
    # --- the IDE's own, which a headless caller drops ------------------------
    CoreOption(
        name="theme",
        decls=("-t", "--theme"),
        kwargs={
            "default": DEFAULT_THEME,
            "show_default": True,
            "help": RuntimeValue("theme_help"),
        },
        harlequin=On(),
    ),
    CoreOption(
        name="viewer_max_rows",
        decls=("--viewer-max-rows",),
        kwargs={
            "default": DEFAULT_VIEWER_MAX_ROWS,
            "type": click.IntRange(min=UNLIMITED),
            "help": (
                "Set the maximum number of rows that can be loaded into "
                "Harlequin's Results Viewer. Set to -1 for no limit. Default is "
                f"{DEFAULT_VIEWER_MAX_ROWS:,}"
            ),
        },
        harlequin=On(),
    ),
    CoreOption(
        name="show_files",
        decls=("--show-files", "-f"),
        kwargs={
            "type": click.Path(
                exists=True, file_okay=False, dir_okay=True, path_type=Path
            ),
            "help": (
                "The path to a directory to show in a file tree viewer in the "
                "Data Catalog."
            ),
        },
        harlequin=On(),
    ),
    CoreOption(
        name="show_s3",
        decls=("--show-s3", "--s3"),
        kwargs={
            "help": (
                "The bucket name or URI, or the keyword `all` to show s3 objects "
                "in the Data Catalog."
            )
        },
        harlequin=On(),
    ),
    CoreOption(
        name="keymap_name",
        decls=("--keymap-name",),
        kwargs={
            "multiple": True,
            "default": DEFAULT_KEYMAP_NAMES,
            "help": (
                "The name of a keymap plugin to load. Repeat this option to load "
                "multiple keymaps. Keymaps listed last will override earlier ones. "
                "For example, to tweak the default keymap, use '--keymap-name "
                "vscode --keymap-name my_keys'"
            ),
        },
        harlequin=On(),
    ),
    CoreOption(
        name="locale",
        decls=("--locale",),
        kwargs={
            "help": (
                "Provide a locale string (e.g., `en_US.UTF-8`) to override "
                "the system locale for number formatting."
            )
        },
        harlequin=On(),
    ),
    CoreOption(
        name="no_download_tzdata",
        decls=("--no-download-tzdata",),
        kwargs={
            "is_flag": True,
            "help": (
                "(Windows Only) Prevent Harlequin from looking for an IANA "
                "timezone database, or downloading one if it is missing. "
                "Harlequin may fail to load timestamptz values into the Results "
                "Viewer."
            ),
        },
        harlequin=On(),
    ),
    # --- the IDE's mini apps, which are not profile keys ---------------------
    CoreOption(
        name="config",
        decls=("--config",),
        cli_only=True,
        kwargs={
            "is_flag": True,
            "expose_value": True,
            "callback": RuntimeValue("config_wizard"),
            "help": (
                "Run the configuration wizard to create or update a Harlequin "
                "config file."
            ),
        },
        harlequin=On(),
    ),
    CoreOption(
        name="keys",
        decls=("--keys",),
        cli_only=True,
        kwargs={
            "is_flag": True,
            "expose_value": True,
            "callback": RuntimeValue("keys_app"),
            "help": (
                "Run the key binding config app to create or update a Harlequin keymap."
            ),
        },
        harlequin=On(),
    ),
)
"""Every core option, in the order hsql's `--help` lists them; the IDE's own
last, since its help renders from the groups in `harlequin.cli`."""

BY_NAME = {option.name: option for option in CORE_OPTIONS}
"""Every declaration, under its profile key."""


def attach_core_options(
    cmd: click.Command,
    command: str,
    *,
    runtime_values: Mapping[str, Any],
    option_cls: type[click.Option] | None = None,
    argument_cls: type[click.Argument] | None = None,
) -> None:
    """Put every option `command` declares on an already-built command, in
    declaration order. `option_cls` is its `click.Option` subclass, if any."""
    for option in CORE_OPTIONS:
        declaration = option.to_click(
            command,
            runtime_values,
            option_cls=option_cls,
            argument_cls=argument_cls,
        )
        if declaration is not None:
            declaration(cmd)


def first_pass_options(command: str) -> list[click.Option]:
    """The options the first pass reads, as the copies it probes argv with.

    Not `to_click()`: the pass runs before the command exists, so there are no
    runtime values to resolve, and the probe has to survive an argv the command
    would refuse -- a `click.Choice` of installed adapters, a path that must
    exist, or a callback would each abort it. A path stays a path, so what the
    pass hands `load_profile()` is one.
    """
    probe: list[click.Option] = []
    for option in CORE_OPTIONS:
        command_config = option.config_for_command(command)
        if not option.first_pass or command_config is None:
            continue
        kwargs = {**option.kwargs, **command_config.kwargs}
        declared_type = kwargs.get("type")
        probe.append(
            click.Option(
                [
                    decl
                    for decl in (command_config.decls or option.decls)
                    if decl.startswith("-")
                ]
                + [option.name],
                is_flag=bool(kwargs.get("is_flag")),
                envvar=kwargs.get("envvar"),
                type=click.Path(path_type=Path)
                if isinstance(declared_type, click.Path)
                else None,
            )
        )
    return probe


def _names(group: Group) -> frozenset[str]:
    return frozenset(option.name for option in CORE_OPTIONS if option.group is group)


CONNECTION_OPTIONS = _names(Group.CONNECTION)
"""Opened with the connection, so `--serve` takes them. Every adapter option
is one too; `connection_option_names()` joins the two."""

CONFIG_OPTIONS = _names(Group.CONFIG)
"""Which file and which profile the rest are read from: names rather than
values, so what a profile holds decides its group."""

PER_REQUEST_OPTIONS = _names(Group.PER_REQUEST)
"""Read per invocation, so a client sends them and `--serve` takes none."""

SERVER_OPTIONS = _names(Group.SERVER)
"""Set per server, and bound to its lifetime."""

ROLE_OPTIONS = _names(Group.ROLE)
"""The two spellings that say which process an invocation is."""

SSH_KEYS = tuple(
    option.name for option in CORE_OPTIONS if option.name.startswith("ssh_")
)
"""The tunnel's profile keys: the ones named for it. `take_ssh_keys()` takes
them off before an adapter is handed the rest."""

CLI_ONLY_SSH_KEYS = tuple(name for name in SSH_KEYS if BY_NAME[name].cli_only)
"""SSH keys a config file may not answer: `ssh_allow_reuse` turns off the
check that the local port is nobody else's listener, and a cloned repository
supplies a config file."""

CLI_ONLY_SESSION_KEYS = tuple(
    option.name
    for option in CORE_OPTIONS
    if option.cli_only and option.hsql is not None and option.name not in SSH_KEYS
)
"""The keys that decide which process runs an invocation. hsql reads them
off argv before any config file, so a profile could not answer them."""

TUI_ONLY_KEYS = tuple(
    option.name
    for option in CORE_OPTIONS
    if option.hsql is None and not option.cli_only
)
"""Profile keys the IDE reads and a headless caller must drop.

One profile serves both commands, and `locale` above all: output that varied
with `LC_ALL` is output a caller could not predict.
"""
