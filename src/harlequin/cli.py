from typing import List, Union

import click

from harlequin import Harlequin


@click.command()
@click.version_option(package_name="harlequin")
@click.option(
    "-r",
    "-readonly",
    "--read-only",
    is_flag=True,
    help="Open the database file in read-only mode.",
)
@click.option(
    "-u",
    "-unsigned",
    "--allow-unsigned-extensions",
    is_flag=True,
    help="Allow loading unsigned extensions",
)
@click.option(
    "-e",
    "--extension",
    multiple=True,
    help=(
        "Install and load the named DuckDB extension when starting "
        "Harlequin. To install multiple extensions, repeat this option."
    ),
)
@click.option(
    "--force-install-extensions",
    is_flag=True,
    help="Force install all extensions passed with -e.",
)
@click.option(
    "--custom-extension-repo",
    help=(
        "A value to pass to DuckDB's custom_extension_repository variable. "
        "Will be set before installing any extensions that are passed using -e."
    ),
)
@click.option(
    "-t",
    "--theme",
    default="monokai",
    help=(
        "Set the theme (colors) of the text editor. "
        "Must be the name of a Pygments style; see "
        "https://pygments.org/styles/. Defaults to "
        "monokai."
    ),
)
@click.option(
    "--md_token",
    help=(
        "MotherDuck Token. Pass your MotherDuck service token in this option, or "
        "set the `motherduck_token` environment variable."
    ),
)
@click.option(
    "--md_saas",
    is_flag=True,
    help="Run MotherDuck in SaaS mode (no local privileges).",
)
@click.argument(
    "db_path",
    nargs=-1,
    type=click.Path(path_type=str),
)
def harlequin(
    db_path: List[str],
    read_only: bool,
    allow_unsigned_extensions: bool,
    extension: List[str],
    force_install_extensions: bool,
    custom_extension_repo: Union[str, None],
    theme: str,
    md_token: Union[str, None],
    md_saas: bool,
) -> None:
    if not db_path:
        db_path = [":memory:"]
    tui = Harlequin(
        db_path=db_path,
        read_only=read_only,
        extensions=extension,
        force_install_extensions=force_install_extensions,
        custom_extension_repo=custom_extension_repo,
        theme=theme,
        md_token=md_token,
        md_saas=md_saas,
        allow_unsigned_extensions=allow_unsigned_extensions,
    )
    tui.run()
