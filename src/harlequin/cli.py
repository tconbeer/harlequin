from pathlib import Path
from typing import List, Tuple, Union

import click

from harlequin import Harlequin
from harlequin.config import get_init_script


@click.command()
@click.version_option(package_name="harlequin")
@click.argument(
    "db_path",
    nargs=-1,
    type=click.Path(path_type=str),
)
@click.option(
    "-t",
    "--theme",
    default="monokai",
    show_default=True,
    help=(
        "Set the theme (colors) of the text editor. "
        "Must be the name of a Pygments style; see "
        "https://pygments.org/styles/"
    ),
)
@click.option(
    "-i",
    "-init",
    "--init-path",
    default="~/.duckdbrc",
    show_default=True,
    type=click.Path(
        exists=False,
        file_okay=True,
        dir_okay=False,
        path_type=Path,
    ),
    help=(
        "The path to an initialization script. On startup, Harlequin will execute "
        "the commands in the script against the attached database."
    ),
)
@click.option(
    "--no-init",
    is_flag=True,
    help="Start Harlequin without executing the initialization script.",
)
@click.option(
    "--limit",
    "-l",
    default=100_000,
    type=click.IntRange(min=0),
    help=(
        "Set the maximum number of rows that can be loaded into Harlequin's Results "
        "Viewer. Set to 0 for no limit. Default is 100,000"
    ),
)
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
def harlequin(
    db_path: Tuple[str],
    theme: str,
    init_path: Path,
    no_init: bool,
    limit: int,
    read_only: bool,
    allow_unsigned_extensions: bool,
    extension: List[str],
    force_install_extensions: bool,
    custom_extension_repo: Union[str, None],
    md_token: Union[str, None],
    md_saas: bool,
) -> None:
    if not db_path:
        db_path = (":memory:",)

    tui = Harlequin(
        db_path=db_path,
        init_script=get_init_script(init_path, no_init),
        max_results=limit,
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
