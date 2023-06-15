from pathlib import Path
from typing import List

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
@click.argument(
    "db_path",
    nargs=-1,
    type=click.Path(path_type=Path),
)
def harlequin(db_path: List[Path], read_only: bool, theme: str) -> None:
    if not db_path:
        db_path = [Path(":memory:")]
    tui = Harlequin(db_path=db_path, read_only=read_only, theme=theme)
    tui.run()
