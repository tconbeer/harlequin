from __future__ import annotations

import sys
from typing import Any, Callable

import rich_click as click

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter

if sys.version_info < (3, 10):
    from importlib_metadata import entry_points, version
else:
    from importlib.metadata import entry_points, version

# configure the rich click interface (mostly --help options)
DOCS_URL = "https://harlequin.sh/docs/getting-started"
GREEN = "#45FFCA"
YELLOW = "#FEFFAC"
PINK = "#FFB6D9"
PURPLE = "#D67BFF"

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
            "options": ["--adapter", "--theme", "--limit", "--version", "--help"],
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


def build_cli() -> click.Command:
    """
    Loads installed adapters and constructs a click Command that includes options
    defined by all adapters.

    Returns: click.Command
    """
    adapter_eps = entry_points(group="harlequin.adapter")
    adapters: dict[str, type[HarlequinAdapter]] = {}

    for ep in adapter_eps:
        try:
            adapters.update({ep.name: ep.load()})
        except ImportError as e:
            print(
                f"Harlequin could not load the installed plug-in named {ep.name}."
                f"\n\n{e}"
            )

    @click.command()
    @click.version_option(package_name="harlequin", message=_version_option())
    @click.argument(
        "conn_str",
        nargs=-1,
    )
    @click.option(
        "-t",
        "--theme",
        default="monokai",
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
        default=100_000,
        type=click.IntRange(min=0),
        help=(
            "Set the maximum number of rows that can be loaded into Harlequin's "
            "Results Viewer. Set to 0 for no limit. Default is 100,000"
        ),
    )
    @click.option(
        "-a",
        "--adapter",
        default="duckdb",
        show_default=True,
        type=click.Choice(list(adapters.keys()), case_sensitive=False),
        help=(
            "The name of an installed database adapter plug-in "
            "to use to connect to the database at CONN_STR."
        ),
    )
    @click.pass_context
    def inner_cli(
        ctx: click.Context,
        conn_str: tuple[str],
        theme: str,
        limit: int,
        adapter: str,
        **kwargs: Any,
    ) -> None:
        """
        This command starts the Harlequin IDE.

        [bold #FFB6D9]CONN_STR[/] [#D67BFF](TEXT MULTIPLE)[/][dim]: One or more
        connection strings (or paths to local db files) for databases to open with
        Harlequin.[/]
        """
        # prune the kwargs to only those that don't have their default arguments
        params = list(kwargs.keys())
        for k in params:
            if (
                ctx.get_parameter_source(k)
                == click.core.ParameterSource.DEFAULT  # type: ignore
            ):
                kwargs.pop(k)

        # load and instantiate the adapter
        adapter_cls: type[HarlequinAdapter] = adapters[adapter]
        adapter_instance = adapter_cls(conn_str=conn_str, **kwargs)

        tui = Harlequin(
            adapter=adapter_instance,
            max_results=limit,
            theme=theme,
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
