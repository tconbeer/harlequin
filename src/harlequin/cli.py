from pathlib import Path
import click
import subprocess

@click.command()
@click.version_option(package_name="harlequin")
@click.argument(
    "db_path",
    default=":memory:",
    nargs=1,
    type=click.Path(path_type=Path),
)
def harlequin(db_path: Path) -> None:
    subprocess.run(["duckdb", str(db_path)])
