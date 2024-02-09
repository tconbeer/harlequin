from . import Table

def write_feather(
    df: Table,
    dest: str,
    compression: str | None = None,
    compression_level: int | None = None,
    chunksize: int | None = None,
    version: int = 2,
) -> None: ...
