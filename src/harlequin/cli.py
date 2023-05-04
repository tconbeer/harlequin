from pathlib import Path

import click

from harlequin import Harlequin


@click.command()
@click.version_option(package_name="harlequin")
@click.argument(
    "db_path",
    default=":memory:",
    nargs=1,
    type=click.Path(path_type=Path),
)
def harlequin(db_path: Path) -> None:
    tui = Harlequin(db_path=db_path)
    tui.run()
