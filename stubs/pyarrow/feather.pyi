from . import Table

def write_feather(
    df: Table,
    dest: str,
    compression: str | None = None,
    compression_level: int | None = None,
    chunksize: int | None = None,
    version: int = 2,
) -> None: ...
def read_table(
    source: str,
    columns: list[str] | None = None,
    memory_map: bool = False,
) -> Table: ...
