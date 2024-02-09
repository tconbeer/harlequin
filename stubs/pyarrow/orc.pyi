from __future__ import annotations

from typing import Literal

from . import NativeFile, Table

def write_table(
    table: Table,
    where: str | NativeFile,
    *,
    file_version: Literal["0.11", "0.12"] = "0.12",
    batch_size: int = 1024,
    stripe_size: int = 67108864,
    compression: Literal[
        "uncompressed", "snappy", "zlib", "lz4", "zstd"
    ] = "uncompressed",
    compression_block_size: int = 65536,
    compression_strategy: Literal["speed", "compression"] = "speed",
    row_index_stride: int = 10000,
    padding_tolerance: float = 0.0,
    dictionary_key_size_threshold: float = 0.0,
    bloom_filter_columns: list[str] | None = None,
    bloom_filter_fpp: float = 0.05,
) -> None: ...
