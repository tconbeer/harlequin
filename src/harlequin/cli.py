from __future__ import annotations

import sys
from importlib.metadata import entry_points, version
from pathlib import Path
from typing import Any, Sequence

import rich_click as click

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.catalog_cache import get_connection_hash
from harlequin.colors import GREEN, PINK, PURPLE, VALID_THEMES, YELLOW
from harlequin.config import get_config_for_profile
from harlequin.config_wizard import wizard
from harlequin.exception import (
    HarlequinConfigError,
    HarlequinLocaleError,
    HarlequinTzDataError,
    pretty_print_error,
)
from harlequin.keys_app import HarlequinKeys
from harlequin.locale_manager import set_locale
from harlequin.options import AbstractOption
from harlequin.plugins import load_adapter_plugins
from harlequin.windows_timezone import check_and_install_tzdata

# configure defaults
DEFAULT_ADAPTER = "duckdb"
DEFAULT_LIMIT = 100_000
DEFAULT_THEME = "harlequin"
ALL_THEMES = ", ".join(VALID_THEMES.keys())
DEFAULT_KEYMAP_NAMES = ["vscode"]

# configure the rich click interface (mostly --help options)
DOCS_URL = "https://harlequin.sh/docs/getting-started"

# general
click.rich_click.USE_RICH_MARKUP = True
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

# metavars
click.rich_click.SHOW_METAVARS_COLUMN = False
click.rich_click.APPEND_METAVARS_HELP = True
click.rich_click.STYLE_METAVAR_APPEND = PURPLE
click.rich_click.STYLE_METAVAR_SEPARATOR = PURPLE

# errors
click.rich_click.STYLE_ERRORS_SUGGESTION = "italic"
click.rich_click.ERRORS_SUGGESTION = "Try 'harlequin --help' to view available options."
click.rich_click.ERRORS_EPILOGUE = (
    f"To learn more, visit [link={DOCS_URL}]{DOCS_URL}[/link]"
)

# define main option group (adapter options added to own groups below)
click.rich_click.OPTION_GROUPS = {
    "harlequin": [
        {
            "name": "Harlequin Options",
            "options": [
                "--profile",
                "--adapter",
                "--show-files",
                "--show-s3",
                "--theme",
                "--keymap-name",
                "--limit",
                "--config-path",
                "--locale",
                "--no-download-tzdata",
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
}


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


def build_cli() -> click.Command:
    """
    Loads installed adapters and constructs a click Command that includes options
    defined by all adapters.

    Returns: click.Command
    """
    adapters = load_adapter_plugins()

    @click.command()
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
        "--limit",
        "-l",
        default=DEFAULT_LIMIT,
        type=click.IntRange(min=0),
        help=(
            "Set the maximum number of rows that can be loaded into Harlequin's "
            "Results Viewer. Set to 0 for no limit. Default is 100,000"
        ),
    )
    @click.option(
        "--adapter",
        "-a",
        default=DEFAULT_ADAPTER,
        show_default=True,
        type=click.Choice(list(adapters.keys()), case_sensitive=False),
        help=(
            "The name of an installed database adapter plug-in "
            "to use to connect to the database at CONN_STR."
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
            config, user_defined_keymaps = get_config_for_profile(
                config_path=config_path, profile_name=profile
            )
        except HarlequinConfigError as e:
            pretty_print_error(e)
            ctx.exit(2)

        # prune the kwargs to only those that don't have their default arguments
        params = list(kwargs.keys())
        for k in params:
            if (
                ctx.get_parameter_source(k) == click.core.ParameterSource.DEFAULT  # type: ignore[attr-defined]
            ):
                kwargs.pop(k)
            # conn_str is an arg, not an option, so get_paramter_source is always CLI
            elif k == "conn_str" and kwargs[k] == tuple():
                kwargs.pop(k)

        # merge the config and the cli options
        config.update(kwargs)  # type: ignore[typeddict-item]

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
        max_results: str | int = config.pop("limit", DEFAULT_LIMIT)
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

        # load and instantiate the adapter
        adapter: str = config.pop("adapter", DEFAULT_ADAPTER)
        adapter_cls: type[HarlequinAdapter] = adapters[adapter]
        try:
            adapter_instance = adapter_cls(conn_str=conn_str, **config)  # type: ignore[misc]
        except HarlequinConfigError as e:
            pretty_print_error(e)
            ctx.exit(2)

        connection_id = (
            adapter_instance.connection_id
            if adapter_instance.connection_id is not None
            else get_connection_hash(conn_str, config)
        )

        tui = Harlequin(
            adapter=adapter_instance,
            profile_name=profile,
            keymap_names=keymap_names,
            user_defined_keymaps=user_defined_keymaps,
            connection_hash=connection_id,
            max_results=max_results,
            theme=theme,
            show_files=show_files,
            show_s3=show_s3,
        )
        tui.run()

    # iterate through installed adapters and decorate the inner_cli command
    # with the additional options declared by the adapter.
    # we load the options into a dict keyed by their name to de-dupe options
    # that may be passed by multiple adapters.
    options: dict[str, AbstractOption] = {}
    for adapter_name, adapter_cls in sorted(adapters.items()):
        option_name_list: list[str] = []
        if adapter_cls.ADAPTER_OPTIONS is not None:
            for option in adapter_cls.ADAPTER_OPTIONS:
                existing = options.get(option.name, None)
                if existing is not None:
                    options[option.name] = existing.merge(option)
                else:
                    options[option.name] = option
                option_name_list.append(f"--{option.name}")
        click.rich_click.OPTION_GROUPS["harlequin"].append(
            {"name": f"{adapter_name} Adapter Options", "options": option_name_list}
        )

    fn = inner_cli
    for option in options.values():
        fn = option.to_click()(fn)  # type: ignore[assignment]

    return fn


def harlequin() -> None:
    """
    The main entrypoint for the Harlequin IDE. Builds and executes the click Command.
    """
    cli = build_cli()
    cli()
