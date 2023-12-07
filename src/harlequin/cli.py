from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Sequence

import rich_click as click

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.colors import GREEN, PINK, PURPLE, YELLOW
from harlequin.config import get_config_for_profile
from harlequin.config_wizard import wizard
from harlequin.exception import HarlequinConfigError, pretty_print_error
from harlequin.plugins import load_plugins

if sys.version_info < (3, 10):
    from importlib_metadata import entry_points, version
else:
    from importlib.metadata import entry_points, version

# configure defaults
DEFAULT_ADAPTER = "duckdb"
DEFAULT_LIMIT = 100_000
DEFAULT_THEME = "harlequin"

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
                "--config-path",
                "--adapter",
                "--theme",
                "--limit",
                "--config",
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
        adapter_versions.update({ep.name: ep.dist.version})

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
    wizard()
    ctx.exit(0)


def build_cli() -> click.Command:
    """
    Loads installed adapters and constructs a click Command that includes options
    defined by all adapters.

    Returns: click.Command
    """
    adapters = load_plugins()

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
    )
    @click.option(
        "-t",
        "--theme",
        default=DEFAULT_THEME,
        show_default=True,
        help=(
            "Set the theme (colors) of the Harlequin IDE. "
            "Must be the name of a Pygments style; see "
            "https://pygments.org/styles/"
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
        "-a",
        "--adapter",
        default=DEFAULT_ADAPTER,
        show_default=True,
        type=click.Choice(list(adapters.keys()), case_sensitive=False),
        help=(
            "The name of an installed database adapter plug-in "
            "to use to connect to the database at CONN_STR."
        ),
    )
    @click.option(
        "--config",
        help=(
            "Run the configuration wizard to create or update a Harlequin "
            "config file."
        ),
        is_flag=True,
        callback=_config_wizard_callback,
        expose_value=True,
        is_eager=True,
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
            config = get_config_for_profile(config_path=config_path, profile=profile)
        except HarlequinConfigError as e:
            pretty_print_error(e)
            ctx.exit(2)

        # prune the kwargs to only those that don't have their default arguments
        params = list(kwargs.keys())
        for k in params:
            if (
                ctx.get_parameter_source(k)
                == click.core.ParameterSource.DEFAULT  # type: ignore
            ):
                kwargs.pop(k)
            # conn_str is an arg, not an option, so get_paramter_source is always CLI
            elif k == "conn_str" and kwargs[k] == tuple():
                kwargs.pop(k)

        # merge the config and the cli options
        config.update(kwargs)

        # load and instantiate the adapter
        adapter = config.pop("adapter", DEFAULT_ADAPTER)
        conn_str: Sequence[str] = config.pop("conn_str", tuple())  # type: ignore
        if isinstance(conn_str, str):
            conn_str = (conn_str,)
        adapter_cls: type[HarlequinAdapter] = adapters[adapter]  # type: ignore
        try:
            adapter_instance = adapter_cls(conn_str=conn_str, **config)
        except HarlequinConfigError as e:
            pretty_print_error(e)
            ctx.exit(2)

        tui = Harlequin(
            adapter=adapter_instance,
            max_results=config.get("limit", DEFAULT_LIMIT),  # type: ignore
            theme=config.get("theme", DEFAULT_THEME),  # type: ignore
        )
        tui.run()

    # iterate through installed adapters and decorate the inner_cli command
    # with the additional options declared by the adapter.
    # we load the options into a dict keyed by their name to de-dupe options
    # that may be passed by multiple adapters.
    options: dict[str, Callable[[click.Command], click.Command]] = {}
    for adapter_name, adapter_cls in adapters.items():
        option_name_list: list[str] = []
        if adapter_cls.ADAPTER_OPTIONS is not None:
            for option in adapter_cls.ADAPTER_OPTIONS:
                options.update({option.name: option.to_click()})
                option_name_list.append(f"--{option.name}")
        click.rich_click.OPTION_GROUPS["harlequin"].append(
            {"name": f"{adapter_name} Adapter Options", "options": option_name_list}
        )

    fn = inner_cli
    for click_opt in options.values():
        fn = click_opt(fn)  # type: ignore

    return fn


def harlequin() -> None:
    """
    The main entrypoint for the Harlequin IDE. Builds and executes the click Command.
    """
    cli = build_cli()
    cli()
