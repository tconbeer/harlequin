"""Every option the two commands declare, in one place.

An adapter declares its options once, in `ADAPTER_OPTIONS`, and both commands
attach them; this is the same thing for the options core owns. A declaration
here says which command takes the option, how that command spells it, and the
flags that decide where its value may come from and where it may be used --
so the answers travel with the option instead of in a name list somewhere
else, where the two drift.

`hsql`'s `group` is the one that carries the most: it says *when* an option's
value is read, and so whether `--serve` accepts it, whether a served request
may carry it, and whether it is compared against the session's connection
identity. An option hsql declares cannot be declared without one, which is
what the name lists could not enforce. `docs/cli-options.md` is the reference,
and the checklist for adding an option.

What a command still owns is the part only it can answer: a `click.Choice` of
what is installed, a callback that reaches into its own module, help that
names a value computed at run time. Those arrive as `Supplied`, filled in by
`attach_core_options()`, which is also why nothing here imports either
command -- or anything the headless CLI may not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import click

from harlequin.config import DEFAULT_ADAPTER, DEFAULT_SSH_TIMEOUT, UNLIMITED

HARLEQUIN = "harlequin"
HSQL = "hsql"
"""The two commands, as `CoreOption` names them."""

DEFAULT_THEME = "harlequin"
DEFAULT_KEYMAP_NAMES = ["vscode"]
DEFAULT_VIEWER_MAX_ROWS = 100_000

DEFAULT_FORMAT = "table"

DEFAULT_LIMIT = 500
"""Small on purpose: fetching a million rows to print forty of them is waste.

`--limit` is the *hard* limit -- `cursor.set_limit()`, so fewer rows leave the
database -- and it is the same promise the `limit` key makes in the IDE. `-1`
is unlimited, and `0` fetches a header and no rows, which is how a caller asks
what a query's columns are.
"""

DEFAULT_IDLE_TIMEOUT = 1800.0
DEFAULT_MAX_LIFETIME = 28800.0
"""How long a session waits with nothing to do, and how long it runs at all.

A session is a live authenticated connection, so it is bounded unless an
operator says otherwise; `0` is how they say so.
"""


class Group(Enum):
    """When hsql reads an option's value, which is what decides where it goes.

    `--serve` takes the connection and server groups and refuses the
    per-request one; a served request is the other way round. An option in
    neither group, or in two, would be one nobody refuses -- or one both do.
    """

    CONNECTION = "connection"
    """Read once, when the connection opens, so a session records it."""

    PER_REQUEST = "per-request"
    """Read on every invocation, so a session's client sends it."""

    SERVER = "server"
    """Read once, at start-up, and bound to a server's lifetime."""

    ROLE = "role"
    """Read to decide which process runs the invocation."""

    CONFIG = "config"
    """Read to pick the file and profile the other values come from."""


@dataclass(frozen=True)
class Supplied:
    """A click keyword only the command building the option can fill in.

    `attach_core_options()` replaces it with `supplied[key]`, so a declaration
    can name a choice of what is installed, or a callback, without this module
    importing either command.
    """

    key: str


@dataclass(frozen=True)
class On:
    """One command's declaration of an option.

    Empty where the command spells it exactly as the shared declaration does;
    `decls` replaces the shared spellings and `kwargs` are merged over the
    shared keywords, which is where the two commands' help text differs.
    """

    decls: tuple[str, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CoreOption:
    """One option of the `harlequin` or `hsql` command, and how it may be used.

    `name` is the click parameter name, which is also the profile key a config
    file writes it under. `harlequin` and `hsql` say which command declares it;
    a command whose entry is None does not have it at all.
    """

    name: str
    decls: tuple[str, ...] = ()
    group: Group | None = None
    """When hsql reads it. Required of an option hsql declares, and None of one
    it does not, so the five groups are exactly hsql's parameters."""

    kwargs: Mapping[str, Any] = field(default_factory=dict)
    harlequin: On | None = None
    hsql: On | None = None
    cli_only: bool = False
    """Whether a config file is refused it. True for an option whose value
    decides which process runs the invocation, or which a config file
    discovered in the working directory should not be able to weaken."""

    argument: bool = False
    """Whether it is positional. `CONN_STR` is the only one."""

    first_pass: bool = False
    """Whether the first pass reads it, before there is a command to parse
    with: an option that decides which adapter's options this command carries,
    or whether it reads a profile at all."""

    supplied_param: str | None = None
    """A ready-made click decorator the command supplies instead, under this
    key. `--version` is the only one: its message is the command's own."""

    def __post_init__(self) -> None:
        if self.harlequin is None and self.hsql is None:
            raise ValueError(f"{self.name} is declared by neither command.")
        if (self.hsql is None) is not (self.group is None):
            raise ValueError(
                f"{self.name} must declare a group if and only if hsql takes it."
            )

    def on(self, command: str) -> On | None:
        """How `command` declares it, or None if that command does not."""
        if command == HARLEQUIN:
            return self.harlequin
        if command == HSQL:
            return self.hsql
        raise ValueError(f"{command!r} is neither command.")

    def to_click(
        self,
        command: str,
        supplied: Mapping[str, Any],
        *,
        option_cls: type[click.Option] | None = None,
        argument_cls: type[click.Argument] | None = None,
    ) -> Callable[[click.Command], click.Command] | None:
        """The decorator that puts this option on `command`, or None.

        The decorators click's own `@option` builds append straight to a
        `Command`'s params, which is how `attach_core_options()` controls the
        order they arrive in.
        """
        spelling = self.on(command)
        if spelling is None:
            return None
        if self.supplied_param is not None:
            decorator: Callable[[click.Command], click.Command] = _from_the_command(
                supplied, self.supplied_param, name=self.name
            )
            return decorator
        decls = spelling.decls or self.decls
        kwargs = _fill_in({**self.kwargs, **spelling.kwargs}, supplied, name=self.name)
        if self.argument:
            if argument_cls is not None:
                kwargs["cls"] = argument_cls
            return click.argument(*decls, **kwargs)
        if option_cls is not None:
            kwargs["cls"] = option_cls
        return click.option(*decls, **kwargs)


def _fill_in(
    kwargs: Mapping[str, Any], supplied: Mapping[str, Any], *, name: str
) -> dict[str, Any]:
    """The declared keywords, with what only the command knows filled in."""
    return {
        key: _from_the_command(supplied, value.key, name=name)
        if isinstance(value, Supplied)
        else value
        for key, value in kwargs.items()
    }


def _from_the_command(supplied: Mapping[str, Any], key: str, *, name: str) -> Any:
    """What the command was to supply, or a refusal naming what it left out."""
    if key not in supplied:
        raise KeyError(f"{name} needs {key!r} from the command that declares it.")
    return supplied[key]


CORE_OPTIONS: Sequence[CoreOption] = (
    CoreOption(
        name="version",
        group=Group.PER_REQUEST,
        supplied_param="version_option",
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
            "callback": Supplied("record_source"),
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
            "callback": Supplied("record_source"),
            "metavar": "PATH",
            "help": "Execute SQL from a file, or from stdin for `-`. Repeatable.",
        },
        hsql=On(),
    ),
    CoreOption(
        name="output",
        decls=("-o", "--output"),
        group=Group.PER_REQUEST,
        harlequin=On(
            kwargs={
                "type": click.Path(file_okay=True, dir_okay=True, path_type=Path),
                "help": "The default directory or file path for the Data Exporter.",
            }
        ),
        hsql=On(
            kwargs={
                "metavar": "PATH",
                "help": (
                    "Write results to PATH instead of stdout. Accepts a file "
                    "or directory."
                ),
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
            "type": Supplied("formats"),
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
        harlequin=On(
            kwargs={
                "help": (
                    "Select a profile from an available config file to load its "
                    "values. Other options passed here will take precedence over "
                    "those loaded from the profile. Use the special profile named "
                    "None to use Harlequin's defaults, instead of the default "
                    "profile specified in the config file."
                )
            }
        ),
        hsql=On(
            kwargs={
                "help": (
                    "Load a profile from an available config file. Options passed "
                    "here take precedence over the profile's. Use the profile "
                    "named None for Harlequin's defaults instead of the config "
                    "file's default profile."
                )
            }
        ),
    ),
    # --- the connection ------------------------------------------------------
    CoreOption(
        name="adapter",
        group=Group.CONNECTION,
        first_pass=True,
        kwargs={
            "default": DEFAULT_ADAPTER,
            "show_default": True,
            "type": Supplied("adapters"),
        },
        harlequin=On(
            decls=("--adapter", "-a"),
            kwargs={
                "help": (
                    "The name of an installed database adapter plug-in "
                    "to use to connect to the database at CONN_STR."
                )
            },
        ),
        hsql=On(
            decls=("-a", "--adapter"),
            kwargs={
                "metavar": "NAME",
                "help": "The installed adapter plug-in to connect with.",
            },
        ),
    ),
    CoreOption(
        name="read_only",
        group=Group.CONNECTION,
        kwargs={"is_flag": True},
        harlequin=On(
            decls=("--read-only", "-r", "read_only"),
            kwargs={
                "help": (
                    "Connect read-only, and refuse to start at all if the adapter "
                    "cannot. To check an adapter's capabilities, use `hsql --info`."
                )
            },
        ),
        hsql=On(
            decls=("-r", "--read-only", "read_only"),
            kwargs={
                "help": (
                    "Connect read-only, and refuse to run at all if the adapter "
                    "cannot. To check an adapter's capabilities, use --info."
                )
            },
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
            )
        },
        harlequin=On(),
        hsql=On(kwargs={"metavar": "TEXT"}),
    ),
    CoreOption(
        name="ssh_forward",
        decls=("--ssh-forward",),
        group=Group.CONNECTION,
        kwargs={"multiple": True},
        harlequin=On(
            kwargs={
                "help": (
                    "A local forward, spelled as ssh -L takes one: "
                    "LOCAL:HOST:REMOTE. Repeat this option for more than one. "
                    "Omit it when your ssh config already has the LocalForward."
                )
            }
        ),
        hsql=On(
            kwargs={
                "metavar": "TEXT",
                "help": (
                    "A local forward, spelled as ssh -L takes one: "
                    "LOCAL:HOST:REMOTE. Repeatable. Omit it when your ssh config "
                    "has the LocalForward."
                ),
            }
        ),
    ),
    CoreOption(
        name="ssh_batch_mode",
        decls=("--ssh-batch-mode",),
        group=Group.CONNECTION,
        kwargs={"is_flag": True},
        harlequin=On(
            kwargs={
                "help": (
                    "Fail rather than prompt for a passphrase, a password or a "
                    "host key. ssh's own BatchMode."
                )
            }
        ),
        hsql=On(
            kwargs={
                "help": (
                    "Fail rather than prompt for a passphrase, a password or a "
                    "host key. ssh's own BatchMode; set it in scripts, CI and cron."
                )
            }
        ),
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
        kwargs={"type": click.FloatRange(min=0, min_open=True)},
        harlequin=On(
            kwargs={
                "help": (
                    "Seconds to wait for the tunnel's forwards. Default is "
                    f"{DEFAULT_SSH_TIMEOUT:g}"
                )
            }
        ),
        hsql=On(
            kwargs={
                "metavar": "SECONDS",
                "help": (
                    "Seconds to wait for the tunnel's forwards. "
                    f"[default: {DEFAULT_SSH_TIMEOUT:g}]"
                ),
            }
        ),
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
        kwargs={"envvar": "HARLEQUIN_CONFIG_PATH", "show_envvar": True},
        harlequin=On(
            kwargs={
                "type": click.Path(
                    exists=True,
                    file_okay=True,
                    dir_okay=False,
                    resolve_path=True,
                    path_type=Path,
                ),
                "help": (
                    "By default, Harlequin finds files named .harlequin.toml in "
                    "the current directory and the home directory (~) and merges "
                    "them. Use this option to specify the full path to a config "
                    "file at a different location."
                ),
            }
        ),
        hsql=On(
            kwargs={
                "type": click.Path(dir_okay=False, resolve_path=True, path_type=Path),
                "metavar": "PATH",
                "help": "Use this config file instead of the ones hsql discovers.",
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
            "type": Supplied("config_modes"),
            "help": Supplied("config_mode_help"),
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
            "help": Supplied("display_rows_help"),
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
            "help": Supplied("theme_help"),
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
            "callback": Supplied("config_wizard"),
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
            "callback": Supplied("keys_app"),
            "help": (
                "Run the key binding config app to create or update a Harlequin keymap."
            ),
        },
        harlequin=On(),
    ),
)
"""Every core option, in the order hsql's `--help` lists them.

The IDE's own are last: its help is rendered from the option groups in
`harlequin.cli`, so where they sit here is free.
"""

BY_NAME = {option.name: option for option in CORE_OPTIONS}
"""Every declaration, under the profile key it is read from."""


def attach_core_options(
    cmd: click.Command,
    command: str,
    *,
    supplied: Mapping[str, Any],
    option_cls: type[click.Option] | None = None,
    argument_cls: type[click.Argument] | None = None,
) -> None:
    """Put every option `command` declares on an already-built command.

    The same shape as `attach_adapter_options()`, and for the same reason: a
    declaration appends straight to `cmd.params`, so the order options arrive
    in is the order they are declared in above -- which is the order `--help`
    lists them. `option_cls` is the command's own `click.Option` subclass,
    where it has one.
    """
    for option in CORE_OPTIONS:
        declaration = option.to_click(
            command, supplied, option_cls=option_cls, argument_cls=argument_cls
        )
        if declaration is not None:
            declaration(cmd)


def first_pass_options(command: str) -> list[click.Option]:
    """The options the first pass reads, as the copies it probes argv with.

    Spellings, whether a value follows, and an envvar: no more, because the
    pass runs before the command exists and has to survive an argv the command
    would refuse -- a `click.Path` that must exist would abort the whole probe
    over a file the invocation was going to be told about properly. A path
    stays a path, so what the pass hands `load_profile()` is one.
    """
    probe: list[click.Option] = []
    for option in CORE_OPTIONS:
        spelling = option.on(command)
        if not option.first_pass or spelling is None:
            continue
        kwargs = {**option.kwargs, **spelling.kwargs}
        declared_type = kwargs.get("type")
        probe.append(
            click.Option(
                [
                    decl
                    for decl in (spelling.decls or option.decls)
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
"""Opened once, with the connection, so `--serve` takes them. Every adapter
option is one too; `connection_option_names()` joins the two sets."""

CONFIG_OPTIONS = _names(Group.CONFIG)
"""Which file and which profile the other options are read from.

Not connection-time, though a profile usually holds connection-time keys:
these name where values come from rather than being values, so which group
one belongs to is decided by what it resolves to. A profile of nothing but
`format` and `limit` is per-request whichever way it was named, which is what
lets a served `-P` behave the way a discovered `default_profile` does.
"""

PER_REQUEST_OPTIONS = _names(Group.PER_REQUEST)
"""Read on every invocation, so a session's client sends them and `--serve`
takes none."""

SERVER_OPTIONS = _names(Group.SERVER)
"""Set once per server, and bound to its lifetime."""

ROLE_OPTIONS = _names(Group.ROLE)
"""The two spellings that say which process an invocation is."""

SSH_KEYS = tuple(
    option.name for option in CORE_OPTIONS if option.name.startswith("ssh_")
)
"""The profile keys that describe an SSH tunnel, which both commands read.

The tunnel's keys are the ones named for it, which is what `take_ssh_keys()`
takes off a merged config before an adapter is handed the rest.
"""

CLI_ONLY_SSH_KEYS = tuple(name for name in SSH_KEYS if BY_NAME[name].cli_only)
"""SSH keys a config file may not answer.

Config files are discovered in the working directory, so a cloned repository
supplies one. `ssh_allow_reuse` turns off the check that the local port is not
already someone else's listener, and a default that fails closed has to stay
the caller's to turn off.
"""

CLI_ONLY_SESSION_KEYS = tuple(
    option.name
    for option in CORE_OPTIONS
    if option.cli_only and option.hsql is not None and option.name not in SSH_KEYS
)
"""The keys that decide which process runs an invocation, read from the
command line alone as `CLI_ONLY_SSH_KEYS` are.

hsql reads all three off argv before it opens a config file, so a profile
that set one would name a session the invocation never reached, turn a query
into a server, or apply only to the runs that never reach a session.
"""

TUI_ONLY_KEYS = tuple(
    option.name
    for option in CORE_OPTIONS
    if option.hsql is None and not option.cli_only
)
"""Profile keys the IDE reads and a headless caller must drop.

One profile serves both commands, so a profile written for the IDE has to work
headless -- these are dropped rather than handed to an adapter as options it
never declared. `locale` in particular is one a headless caller must ignore:
the IDE sets it to group digits for a human, and output that varied with
`LC_ALL` would be output a caller could not predict.
"""
