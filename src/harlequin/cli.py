from __future__ import annotations

import sys
from typing import Any, Callable

import click

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter

if sys.version_info < (3, 10):
    from importlib_metadata import entry_points
else:
    from importlib.metadata import entry_points


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
    @click.version_option(package_name="harlequin")
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

        conn_str TEXT: One or more connection strings (or paths to local db files)
        for databases to open with Harlequin.
        """
        # prune the kwargs to only those that don't have their default arguments
        params = list(kwargs.keys())
        for k in params:
            if ctx.get_parameter_source(k) == click.core.ParameterSource.DEFAULT:
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
    for adapter_cls in adapters.values():
        if adapter_cls.ADAPTER_OPTIONS is not None:
            for option in adapter_cls.ADAPTER_OPTIONS:
                options.update({option.name: option.to_click()})

    fn = inner_cli
    for click_opt in options.values():
        fn = click_opt(fn)

    return fn


def harlequin() -> None:
    """
    The main entrypoint for the Harlequin IDE. Builds and executes the click Command.
    """
    cli = build_cli()
    cli()
