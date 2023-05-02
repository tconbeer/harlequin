from pathlib import Path
import click
from harlequin import SqlClient
import duckdb


@click.command()
@click.version_option(package_name="harlequin")
@click.argument(
    "db_path",
    default=":memory:",
    nargs=1,
    type=click.Path(path_type=Path),
)
def harlequin(db_path: Path) -> None:
    conn = duckdb.connect(str(db_path))
    tui = SqlClient(connection=conn)
    tui.run()
