from __future__ import annotations

import contextlib
import sys
import warnings
from functools import lru_cache
from importlib.metadata import entry_points, version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

import rich_click as click
from rich_click.utils import OptionGroupDict

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.catalog_cache import get_connection_hash
from harlequin.colors import GREEN, PINK, PURPLE, VALID_THEMES, YELLOW
from harlequin.config import (
    DEFAULT_ADAPTER,
    DEFAULT_SSH_TIMEOUT,
    Profile,
    load_profile_and_keymaps,
    merge_profile_with_cli,
    parse_profile_options,
    parse_row_count,
    take_ssh_keys,
)
from harlequin.config_wizard import wizard
from harlequin.exception import (
    HarlequinConfigError,
    HarlequinLocaleError,
    HarlequinSshError,
    HarlequinTzDataError,
    pretty_print_error,
)
from harlequin.first_pass import attach_adapter_options, first_pass
from harlequin.keys_app import HarlequinKeys
from harlequin.locale_manager import set_locale
from harlequin.options import AbstractOption
from harlequin.plugins import adapter_names, load_adapter, load_adapter_plugins
from harlequin.redact import hide_secrets_in
from harlequin.windows_timezone import check_and_install_tzdata

if TYPE_CHECKING:
    from harlequin.ssh import SshTunnel

# configure defaults
DEFAULT_VIEWER_MAX_ROWS = 100_000
DEFAULT_THEME = "harlequin"
ALL_THEMES = ", ".join(VALID_THEMES.keys())
DEFAULT_KEYMAP_NAMES = ["vscode"]

# configure the rich click interface (mostly --help options)
DOCS_URL = "https://harlequin.sh/docs/getting-started"
HEADLESS_DOCS_URL = "https://harlequin.sh/docs/headless"


@lru_cache(maxsize=1)
def _hsql_command() -> click.Command:
    """`hsql`, as a command, so this one can ask what it takes.

    Read rather than copied: the two commands' options drift otherwise. It
    costs ~10ms and imports no adapter, and nothing on the way to `--help` or
    `--version` asks for it.
    """
    from harlequin.hsql.cli import bare_command

    return bare_command()


def hsql_spellings() -> frozenset[str]:
    """Every `--option` spelling `hsql` takes."""
    return frozenset(
        opt
        for param in _hsql_command().params
        for opt in param.opts
        if opt.startswith("-")
    )


def hsql_profile_keys() -> frozenset[str]:
    """Every profile key `hsql` reads for itself.

    One profile serves both commands, so these are keys this command has to
    leave alone rather than hand to an adapter that never declared them.
    """
    return frozenset(param.name for param in _hsql_command().params if param.name)


# general
click.rich_click.TEXT_MARKUP = "rich"
click.rich_click.COLOR_SYSTEM = "truecolor"

click.rich_click.STYLE_OPTIONS_TABLE_LEADING = 1
click.rich_click.STYLE_OPTIONS_TABLE_BOX = "SIMPLE"
click.rich_click.STYLE_OPTIONS_PANEL_BORDER = YELLOW
click.rich_click.STYLE_USAGE = f"bold {YELLOW}"
click.rich_click.STYLE_USAGE_COMMAND = "regular"
click.rich_click.STYLE_HELPTEXT = "regular"
click.rich_click.STYLE_OPTION = PINK
click.rich_click.STYLE_ARGUMENT = PINK
click.rich_click.STYLE_COMMAND = PINK
click.rich_click.STYLE_SWITCH = GREEN

# metavars: drop the metavar column, and append the metavar to the help text instead
click.rich_click.OPTIONS_TABLE_COLUMN_TYPES = [
    "required",
    "opt_long",
    "opt_short",
    "help",
]
click.rich_click.OPTIONS_TABLE_HELP_SECTIONS = [
    "help",
    "deprecated",
    "envvar",
    "default",
    "required",
    "metavar",
]
click.rich_click.STYLE_METAVAR_APPEND = PURPLE
click.rich_click.STYLE_METAVAR_SEPARATOR = PURPLE

# errors
click.rich_click.STYLE_ERRORS_SUGGESTION = "italic"
click.rich_click.ERRORS_SUGGESTION = "Try 'harlequin --help' to view available options."
click.rich_click.ERRORS_EPILOGUE = (
    f"To learn more, visit [link={DOCS_URL}]{DOCS_URL}[/link]"
)

# the option groups every invocation renders, whichever adapters are attached.
# One group per attached adapter is appended to a copy of this in build_cli(),
# which is where what is attached is settled.
HARLEQUIN_OPTION_GROUPS: list[OptionGroupDict] = [
    {
        "name": "Harlequin Options",
        "options": [
            "--profile",
            "--adapter",
            "--read-only",
            "--show-files",
            "--show-s3",
            "--theme",
            "--keymap-name",
            "--viewer-max-rows",
            "--limit",
            "--output",
            "--config-path",
            "--locale",
            "--no-download-tzdata",
        ],
    },
    {
        "name": "SSH Tunnel Options",
        "options": [
            "--ssh-host",
            "--ssh-forward",
            "--ssh-batch-mode",
            "--ssh-allow-reuse",
            "--ssh-timeout",
        ],
    },
    {
        "name": "Mini Apps",
        "options": [
            "--config",
            "--keys",
            "--version",
            "--help",
        ],
    },
]
click.rich_click.OPTION_GROUPS = {"harlequin": [*HARLEQUIN_OPTION_GROUPS]}


class HarlequinCommand(click.RichCommand):
    """The IDE's command, with one thing to say about the other one.

    `harlequin -c "select 1"` is the likeliest first mistake now that there are
    two front doors, and click's "No such option: -c" leaves a reader to guess
    which one they wanted. Naming `hsql` costs one line and saves a search.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.NoSuchOption as e:
            # click has already established the spelling is not this command's,
            # so hsql having it is the whole of the question
            if e.option_name not in hsql_spellings():
                raise
            # a plain UsageError rather than a NoSuchOption with a longer
            # message: click's would append "Did you mean --config?" from the
            # options this command does have, which is the opposite of the
            # point.
            raise click.UsageError(
                f"{e.option_name} is not a harlequin option. Did you mean "
                f"'hsql {e.option_name}'? hsql is Harlequin's headless CLI: it "
                f"runs SQL and exits. See {HEADLESS_DOCS_URL}",
                ctx=ctx,
            ) from None


def _resolve_row_limits(config: Profile) -> tuple[int | None, int | None]:
    """The two limits, as `(query_limit, viewer_max_rows)`.

    They are different questions and no longer one key: `limit` is the hard
    fetch limit, which `hsql` has always meant by it and which the IDE now
    hands to the Run Query Bar; `viewer_max_rows` is the soft cap on what the
    Results Viewer holds of what came back. Unset, the first is a full fetch
    and the second is 100,000 rows -- today's behavior, for a profile that
    names neither.

    Both keys are popped either way: whatever is left goes to the adapter.

    Raises: HarlequinConfigError if either value is not a number of rows.
    """
    limit = config.pop("limit", None)
    viewer_max_rows = config.pop("viewer_max_rows", None)
    return (
        parse_row_count(limit, key="limit") if limit is not None else None,
        parse_row_count(viewer_max_rows, key="viewer_max_rows", zero_is_unlimited=True)
        if viewer_max_rows is not None
        else DEFAULT_VIEWER_MAX_ROWS,
    )


def _version_option() -> str:
    """
    Build the string printed by harlequin --version
    """
    harlequin_version = version("harlequin")
    adapter_eps = entry_points(group="harlequin.adapter")
    adapter_versions: dict[str, str] = {}
    for ep in adapter_eps:
        adapter_versions.update(
            {ep.name: ep.dist.version if ep.dist is not None else "unknown"}
        )

    adapter_output = "\n".join(
        [f"  - {name}, version {version}" for name, version in adapter_versions.items()]
    )

    output = (
        f"harlequin, version {harlequin_version}\n\n"
        "Installed Adapters:\n"
        f"{adapter_output}"
    )

    return output


def _config_wizard_callback(ctx: click.Context, param: Any, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    wizard(ctx.params.get("config_path", None))
    ctx.exit(0)


def _keys_app_callback(ctx: click.Context, param: Any, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    profile_name = ctx.params.get("profile", None)
    if profile_name == "None":
        profile_name = None
    app = HarlequinKeys(
        theme=ctx.params.get("theme", None),
        config_path=ctx.params.get("config_path", None),
        profile_name=profile_name,
        keymap_name=ctx.params.get("keymap_name", None),
    )
    app.run()
    ctx.exit(0)


def _adapter_option_group(
    name: str, adapter_cls: type[HarlequinAdapter]
) -> OptionGroupDict:
    """The rich-click group `--help` renders one adapter's options in."""
    return {
        "name": f"{name} Adapter Options",
        "options": [f"--{option.name}" for option in adapter_cls.ADAPTER_OPTIONS or []],
    }


def _open_tunnel(ctx: click.Context, ssh_config: dict[str, Any]) -> "SshTunnel | None":
    """The tunnel the IDE runs through, or exit having said why there is none.

    Once Textual owns the terminal, `ssh` cannot ask for a passphrase and a 2FA
    push has nowhere to print "check your phone".
    """
    if not ssh_config.get("ssh_host"):
        # nothing to open, and so nothing to import
        return None
    from harlequin.ssh import open_tunnel

    try:
        return open_tunnel(ssh_config)
    except HarlequinConfigError as e:
        pretty_print_error(e)
        ctx.exit(2)
    except HarlequinSshError as e:
        pretty_print_error(e)
        ctx.exit(3)


def build_cli(argv: Sequence[str]) -> click.Command:
    """Build the IDE's command, importing at most one adapter to do it.

    Takes the same arguments click is about to parse, because which adapter's
    connection options belong on the command is a question only the arguments
    can answer. An invocation connects with exactly one adapter, and importing
    the other three to build a command that will never use them is time a user
    waits for nothing -- ~200ms with four installed, and more with the fifth.

    `--help` is the deliberate exception, and takes the other path: it imports
    every installed adapter and documents all of them, so nobody loses the
    ability to discover what an adapter takes. That path gets no faster, which
    is the right trade -- the one that got faster is the one that opens the IDE.
    """
    installed_adapter_names = adapter_names()
    first_pass_config = first_pass(argv, installed_adapter_names, program="harlequin")
    adapters: dict[str, type[HarlequinAdapter]] = {}
    if first_pass_config.wants_help:
        adapters = load_adapter_plugins()
    elif first_pass_config.adapter is not None:
        try:
            adapters = {
                first_pass_config.adapter: load_adapter(first_pass_config.adapter)
            }
        except HarlequinConfigError:
            # a plug-in that will not import is the callback's to report, where
            # there is an exit code: here there is only a command to build, and
            # `_adapter_class()` raises the same error again when it runs.
            pass

    def _adapter_class(name: str) -> type[HarlequinAdapter]:
        """The class for `name`, importing it if the pass above did not.

        Almost always it did -- it read the same `-a` off the same argv, and the
        same `adapter` key off the same profile. What is left is the profile it
        could not see: `load_profile()` stops at the nearest config file that
        defines the profile, where the IDE merges every file that does.

        Raises: HarlequinConfigError for a name nothing installed provides, or
        for a plug-in that will not import.
        """
        loaded = adapters.get(name)
        return loaded if loaded is not None else load_adapter(name)

    @click.command(cls=HarlequinCommand)
    @click.version_option(package_name="harlequin", message=_version_option())
    @click.argument(
        "conn_str",
        nargs=-1,
    )
    @click.option(
        "-P",
        "--profile",
        help=(
            "Select a profile from an available config file to load its values. "
            "Other options passed here will take precedence over those loaded "
            "from the profile. Use the special profile named None to use Harlequin's "
            "defaults, instead of the default profile specified in the config "
            "file."
        ),
    )
    @click.option(
        "--config-path",
        help=(
            "By default, Harlequin finds files named .harlequin.toml in the "
            "current directory and the home directory (~) and merges them. "
            "Use this option to specify the full path to a config file at "
            "a different location."
        ),
        type=click.Path(
            exists=True,
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
            path_type=Path,
        ),
        envvar="HARLEQUIN_CONFIG_PATH",
        show_envvar=True,
    )
    @click.option(
        "-t",
        "--theme",
        default=DEFAULT_THEME,
        show_default=True,
        help=(
            "Set the theme (colors) of the Harlequin IDE. "
            "Must be `harlequin` or the name of a Textual theme: "
            f"{ALL_THEMES}"
        ),
    )
    @click.option(
        "--viewer-max-rows",
        default=DEFAULT_VIEWER_MAX_ROWS,
        type=click.IntRange(min=-1),
        help=(
            "Set the maximum number of rows that can be loaded into Harlequin's "
            "Results Viewer. Set to -1 for no limit. Default is "
            f"{DEFAULT_VIEWER_MAX_ROWS:,}"
        ),
    )
    @click.option(
        "--limit",
        type=click.IntRange(min=-1),
        help=(
            "Default value for the limit control; if set, the limit will be "
            "applied by default. If unset, queries fetch all rows."
        ),
    )
    @click.option(
        "-o",
        "--output",
        type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
        help="The default directory or file path for the Data Exporter.",
    )
    @click.option(
        "--adapter",
        "-a",
        default=DEFAULT_ADAPTER,
        show_default=True,
        type=click.Choice(installed_adapter_names, case_sensitive=False),
        help=(
            "The name of an installed database adapter plug-in "
            "to use to connect to the database at CONN_STR."
        ),
    )
    @click.option(
        "--read-only",
        "-r",
        "read_only",
        is_flag=True,
        help=(
            "Connect read-only, and refuse to start at all if the adapter "
            "cannot. To check an adapter's capabilities, use `hsql --info`."
        ),
    )
    @click.option(
        "--show-files",
        "-f",
        type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
        help=(
            "The path to a directory to show in a file tree viewer in the Data Catalog."
        ),
    )
    @click.option(
        "--show-s3",
        "--s3",
        help=(
            "The bucket name or URI, or the keyword `all` to show s3 objects "
            "in the Data Catalog."
        ),
    )
    @click.option(
        "--keymap-name",
        help=(
            "The name of a keymap plugin to load. Repeat this option to load "
            "multiple keymaps. Keymaps listed last will override earlier ones. "
            "For example, to tweak the default keymap, use '--keymap-name vscode "
            "--keymap-name my_keys'"
        ),
        multiple=True,
        default=DEFAULT_KEYMAP_NAMES,
    )
    @click.option(
        "--ssh-host",
        help=(
            "Open an SSH tunnel to this destination first, and connect through "
            "it. A Host alias, host, user@host or ssh://user@host:port, passed "
            "to ssh verbatim."
        ),
    )
    @click.option(
        "--ssh-forward",
        multiple=True,
        help=(
            "A local forward, spelled as ssh -L takes one: LOCAL:HOST:REMOTE. "
            "Repeat this option for more than one. Omit it when your ssh config "
            "already has the LocalForward."
        ),
    )
    @click.option(
        "--ssh-batch-mode",
        is_flag=True,
        help=(
            "Fail rather than prompt for a passphrase, a password or a host "
            "key. ssh's own BatchMode."
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
        type=click.FloatRange(min=0, min_open=True),
        help=(
            "Seconds to wait for the tunnel's forwards. Default is "
            f"{DEFAULT_SSH_TIMEOUT:g}"
        ),
    )
    @click.option(
        "--config",
        help=(
            "Run the configuration wizard to create or update a Harlequin config file."
        ),
        is_flag=True,
        callback=_config_wizard_callback,
        expose_value=True,
    )
    @click.option(
        "--keys",
        help=("Run the key binding config app to create or update a Harlequin keymap."),
        is_flag=True,
        callback=_keys_app_callback,
        expose_value=True,
    )
    @click.option(
        "--locale",
        help=(
            "Provide a locale string (e.g., `en_US.UTF-8`) to override "
            "the system locale for number formatting."
        ),
    )
    @click.option(
        "--no-download-tzdata",
        help=(
            "(Windows Only) Prevent Harlequin from downloading an IANA timezone "
            "database, even if one is missing. May cause undesired behavior."
        ),
        is_flag=True,
    )
    @click.pass_context
    def inner_cli(
        ctx: click.Context,
        profile: str | None,
        config_path: Path | None,
        **kwargs: Any,
    ) -> None:
        """
        This command starts the Harlequin IDE.

        [bold #FFB6D9]CONN_STR[/] [#D67BFF](TEXT MULTIPLE)[/][dim]: One or more
        connection strings (or paths to local db files) for databases to open with
        Harlequin.[/]
        """
        # load config from any config files
        try:
            profile_config, user_defined_keymaps = load_profile_and_keymaps(
                config_path=config_path, profile_name=profile
            )
        except HarlequinConfigError as e:
            pretty_print_error(e)
            ctx.exit(2)

        explicitly_set = {
            k
            for k in kwargs
            if ctx.get_parameter_source(k) != click.core.ParameterSource.DEFAULT  # type: ignore[attr-defined]
        }

        # the profile's remaining keys are its adapter's options, and the
        # adapter is about to be handed them. Before the merge, because click
        # has already vetted everything typed on the command line.
        adapter_name = str(
            (kwargs.get("adapter", None) if "adapter" in explicitly_set else None)
            or profile_config.get("adapter", None)
            or DEFAULT_ADAPTER
        )
        try:
            adapter_cls = _adapter_class(adapter_name)
            profile_config = parse_profile_options(
                profile_config,
                adapter=adapter_name,
                adapter_options=adapter_cls.ADAPTER_OPTIONS,
                command_options=harlequin_options | hsql_profile_keys(),
            )
        except HarlequinConfigError as e:
            pretty_print_error(e)
            ctx.exit(2)

        config = merge_profile_with_cli(
            profile=profile_config, cli_values=kwargs, explicitly_set=explicitly_set
        )

        # what this process must not print, before anything can print it: an
        # error panel quotes a driver, and ssh's stderr quotes whatever a
        # config file pointed it at
        hide_secrets_in(config, adapter_cls.ADAPTER_OPTIONS)

        # detect and install (if necessary) a tzdatabase on Windows
        if sys.platform == "win32" and not config.pop("no_download_tzdata", None):
            try:
                check_and_install_tzdata()
            except HarlequinTzDataError as e:
                pretty_print_error(e)
                ctx.exit(2)

        # set the locale so we display numbers properly. Empty string uses system
        # default
        locale_config: str = config.pop("locale", "")
        try:
            set_locale(locale_config)
        except HarlequinLocaleError as e:
            pretty_print_error(e)
            ctx.exit(2)

        # remove the harlequin config from the options passed to the adapter
        conn_str: Sequence[str] = config.pop("conn_str", tuple())
        if isinstance(conn_str, str):
            conn_str = (conn_str,)
        try:
            query_limit, viewer_max_rows = _resolve_row_limits(config)
        except HarlequinConfigError as e:
            pretty_print_error(e)
            ctx.exit(2)
        theme: str = config.pop("theme", DEFAULT_THEME)
        keymap_names: list[str] = config.pop("keymap_name", DEFAULT_KEYMAP_NAMES)
        if isinstance(keymap_names, str):
            keymap_names = [keymap_names]
        show_files: Path | str | None = config.pop("show_files", None)
        if show_files is not None:
            try:
                show_files = Path(show_files)
            except TypeError as e:
                pretty_print_error(
                    HarlequinConfigError(msg=str(e), title="Harlequin Config Error")
                )
                ctx.exit(2)
        show_s3: str | None = config.pop("show_s3", None)
        export_path: Path | str | None = config.pop("output", None)
        read_only: bool = bool(config.pop("read_only", False))
        if read_only and not adapter_cls.IMPLEMENTS_READ_ONLY:
            pretty_print_error(
                HarlequinConfigError(
                    msg=(
                        f"{adapter_name} does not declare read-only support, so "
                        "--read-only cannot be honored. See `hsql --info`."
                    ),
                    title="Harlequin could not start.",
                )
            )
            ctx.exit(2)

        # off the config before the adapter is handed the rest of it
        try:
            ssh_config = take_ssh_keys(config, typed=explicitly_set)
        except HarlequinConfigError as e:
            pretty_print_error(e)
            ctx.exit(2)

        # instantiate the adapter, which was named and imported above -- the
        # key comes off either way, because what is left is its options
        config.pop("adapter", None)
        try:
            adapter_instance = adapter_cls(
                conn_str=conn_str, read_only=read_only, **config
            )
        except HarlequinConfigError as e:
            pretty_print_error(e)
            ctx.exit(2)

        with contextlib.ExitStack() as stack:
            if ssh_config.get("ssh_host"):
                from harlequin.ssh import stopping_on_signal

                # entered before the child exists: `start()` blocks for the
                # whole handshake, and a signal caught in that window would
                # otherwise leave `ssh` running
                stack.enter_context(stopping_on_signal())
            tunnel = _open_tunnel(ctx, ssh_config)
            if tunnel is not None:
                stack.callback(tunnel.stop)

            connection_id = (
                adapter_instance.connection_id
                if adapter_instance.connection_id is not None
                else get_connection_hash(conn_str, config)
            )
            if tunnel is not None:
                connection_id = get_connection_hash(
                    (connection_id,), {}, through=tunnel.cache_material()
                )

            tui = Harlequin(
                adapter=adapter_instance,
                profile_name=profile,
                keymap_names=keymap_names,
                user_defined_keymaps=user_defined_keymaps,
                connection_hash=connection_id,
                viewer_max_rows=viewer_max_rows,
                query_limit=query_limit,
                theme=theme,
                show_files=show_files,
                show_s3=show_s3,
                export_path=export_path,
                ssh_tunnel=tunnel,
            )
            tui.run()

    # this command's own options, before any adapter's are added to it
    harlequin_options = {param.name for param in inner_cli.params}

    cmd: click.Command = inner_cli
    adapter_groups: list[OptionGroupDict] = []
    if first_pass_config.wants_help:
        # every installed adapter, and so the one path that has two to
        # reconcile: `merge()` settles an option name both declare (--database
        # against --dbname) into the single option the command carries.
        options: dict[str, AbstractOption] = {}
        for adapter_name, adapter_cls in sorted(adapters.items()):
            for option in adapter_cls.ADAPTER_OPTIONS or []:
                existing = options.get(option.name, None)
                options[option.name] = (
                    existing.merge(option) if existing is not None else option
                )
            adapter_groups.append(_adapter_option_group(adapter_name, adapter_cls))
        for option in options.values():
            option.to_click()(cmd)
    elif adapters:
        # one adapter, and so nothing to reconcile
        ((adapter_name, adapter_cls),) = adapters.items()
        attach_adapter_options(cmd, adapter_cls)
        adapter_groups.append(_adapter_option_group(adapter_name, adapter_cls))

    # rebuilt rather than appended to: which adapters have options on the
    # command is a property of the invocation now, and this dict is read by
    # rich-click when it renders help.
    click.rich_click.OPTION_GROUPS["harlequin"] = [
        *HARLEQUIN_OPTION_GROUPS,
        *adapter_groups,
    ]

    return cmd


def harlequin() -> None:
    """
    The main entrypoint for the Harlequin IDE. Builds and executes the click Command.
    """
    cli = build_cli(sys.argv[1:])
    with warnings.catch_warnings():
        # Two installed adapters can claim the same flag for different options --
        # -u is duckdb's --allow-unsigned-extensions and postgres' --user, and -d
        # is --database for one adapter and --dbname for another. Click warns about
        # each collision, but the flags belong to separate plugins, so there is
        # nothing the user can do about it. Only `--help` carries two adapters'
        # options at once now, so only `--help` can collide.
        warnings.filterwarnings(
            "ignore",
            message="The parameter .* is used more than once",
            category=UserWarning,
        )
        cli()
