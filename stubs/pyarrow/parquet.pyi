from __future__ import annotations

from typing import Any, BinaryIO

from . import NativeFile, Schema, Table
from .compute import Expression
from .dataset import Partitioning
from .fs import FileSystem

class ColumnChunkMetaData:
    compression: str

class RowGroupMetaData:
    def column(self, i: int) -> ColumnChunkMetaData: ...

class FileMetaData:
    def row_group(self, i: int) -> RowGroupMetaData: ...

class ParquetFile:
    def __init__(self, source: str | NativeFile | BinaryIO) -> None: ...
    @property
    def metadata(self) -> FileMetaData: ...
    def read(self) -> Table: ...

def read_table(
    source: str | NativeFile | BinaryIO,
    *,
    columns: list | None = None,
    use_threads: bool = True,
    metadata: FileMetaData | None = None,
    schema: Schema | None = None,
    use_pandas_metadata: bool = False,
    read_dictionary: list | None = None,
    memory_map: bool = False,
    buffer_size: int = 0,
    partitioning: Partitioning | str | list[str] = "hive",
    filesystem: FileSystem | None = None,
    filters: Expression | list[tuple] | list[list[tuple]] | None = None,
    use_legacy_dataset: bool = False,
    ignore_prefixes: list | None = None,
    pre_buffer: bool = True,
    coerce_int96_timestamp_unit: str | None = None,
    decryption_properties: Any | None = None,
    thrift_string_size_limit: int | None = None,
    thrift_container_size_limit: int | None = None,
) -> Table: ...
