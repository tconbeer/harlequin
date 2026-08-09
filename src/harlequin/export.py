"""Writing an Arrow table to a file, or to a stream.

duckdb and pyarrow do the serializing; this module picks the writer and hands
it its options. `tsv` and `jsonl` are the csv and json writers under different
defaults, and any option the caller passes overrides a default.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Mapping, Protocol

from harlequin.exception import HarlequinCopyError

if TYPE_CHECKING:
    import pyarrow as pa


class ExporterCallable(Protocol):
    def __call__(self, data: "pa.Table", dest_path: str, **kwargs: Any) -> None: ...


@dataclass(frozen=True)
class _FileFormat:
    exporter: ExporterCallable
    suffix: str
    """The extension the temp file gets when writing to a stream.

    duckdb reads the extension to decide whether to compress, so a format that
    writes to a path and one that writes to stdout have to agree about it.
    """

    defaults: Mapping[str, Any] = field(default_factory=dict)


def file_format_names() -> list[str]:
    """Every format name `write_file()` accepts, aliases included."""
    return list(_FILE_FORMATS)


def write_file(
    data: "pa.Table",
    path: Path,
    format_name: str,
    options: Mapping[str, Any] | None = None,
) -> None:
    """Write an Arrow table to `path` in `format_name`.

    Zero rows is not an error: an empty file is how "the query matched nothing"
    is told apart from "the query failed".
    """
    fmt = _get_format(format_name)
    fmt.exporter(
        _deduplicate_column_names(data),
        str(path.expanduser()),
        **_merge_options(fmt, options),
    )


def write_stream(
    data: "pa.Table",
    out: BinaryIO,
    format_name: str,
    options: Mapping[str, Any] | None = None,
) -> None:
    """Write an Arrow table to an open binary stream in `format_name`.

    Every writer underneath produces a file, so this one writes a temp file and
    copies it out. `/dev/stdout` would save the copy on Linux and does not exist
    on Windows, and a platform-conditional output path is two paths to test for
    no benefit -- at a few hundred rows the copy is unmeasurable, and at a few
    million duckdb writing a file beats building strings in Python.

    The stream is binary because the bytes are the contract: duckdb writes
    `\\n`, and text-mode translation would turn that into `\\r\\n` on Windows
    for the same query that produced `\\n` everywhere else.
    """
    fmt = _get_format(format_name)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / f"harlequin-export{fmt.suffix}"
        write_file(data, path, format_name, options)
        with path.open("rb") as f:
            shutil.copyfileobj(f, out)


def _get_format(format_name: str) -> _FileFormat:
    try:
        return _FILE_FORMATS[format_name]
    except KeyError as e:
        raise HarlequinCopyError(
            f"{format_name} is not a file format Harlequin can write. "
            f"Try one of: {', '.join(_FILE_FORMATS)}.",
            title="Unknown file format.",
        ) from e


def _merge_options(
    fmt: _FileFormat, options: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The format's defaults, under whatever the caller set explicitly.

    An option left empty is an option not set: the copy dialog renders every
    option of a format whether or not the user touched it, so an untouched text
    input arrives as `""` and must not be passed on as one. `False` is a real
    value, though -- it is how `--no-header` and the array flag are spelled --
    so only `None` and `""` are dropped.
    """
    merged = dict(fmt.defaults)
    merged.update(
        {k: v for k, v in (options or {}).items() if v is not None and v != ""}
    )
    return merged


def _deduplicate_column_names(data: "pa.Table") -> "pa.Table":
    """Rename duplicate columns, which Arrow allows and duckdb does not.

    `select 1 as a, 2 as a` is legal SQL and a legal Arrow table; it is not
    something duckdb will export, or that a csv header or a json object can
    represent unambiguously.
    """
    export_names: list[str] = []
    renamed = False
    for label in data.column_names:
        export_label = label
        n = 0
        while export_label in export_names:
            export_label = f"{label}_{n}"
            n += 1
            renamed = True
        export_names.append(export_label)
    return data.rename_columns(export_names) if renamed else data


def _export_csv(
    data: "pa.Table",
    dest_path: str,
    **kwargs: Any,
) -> None:
    import duckdb

    if kwargs.pop("quoting", False):
        kwargs["quoting"] = "ALL"
    kwargs.setdefault("header", True)
    try:
        relation = duckdb.from_arrow(data)
        relation.write_csv(file_name=dest_path, **kwargs)
    except (duckdb.Error, OSError) as e:
        raise HarlequinCopyError(
            str(e),
            title=("DuckDB raised an error when writing your query to a CSV file."),
        ) from e


def _export_json(
    data: "pa.Table",
    dest_path: str,
    **kwargs: Any,
) -> None:
    import duckdb

    array = ", ARRAY TRUE" if kwargs.get("array") else ""
    compression = (
        f", COMPRESSION {kwargs.get('compression')}"
        if kwargs.get("compression")
        else ""
    )
    date_format = (
        f", DATEFORMAT '{kwargs.get('''date_format''')}'"
        if kwargs.get("date_format")
        else ""
    )
    ts_format = (
        f", TIMESTAMPFORMAT '{kwargs.get('''timestamp_format''')}'"
        if kwargs.get("timestamp_format")
        else ""
    )
    try:
        duckdb.execute(
            f"copy (select * from data) to '{dest_path}' "
            "(FORMAT JSON"
            f"{array}{compression}{date_format}{ts_format}"
            ")"
        )
    except (duckdb.Error, OSError) as e:
        raise HarlequinCopyError(
            str(e),
            title=("DuckDB raised an error when writing your query to a JSON file."),
        ) from e


def _export_parquet(
    data: "pa.Table",
    dest_path: str,
    **kwargs: Any,
) -> None:
    import duckdb

    try:
        relation = duckdb.from_arrow(data)
        relation.write_parquet(
            file_name=dest_path, compression=kwargs.get("compression")
        )
    except (duckdb.Error, OSError) as e:
        raise HarlequinCopyError(
            str(e),
            title=("DuckDB raised an error when writing your query to a Parquet file."),
        ) from e


def _export_orc(
    data: "pa.Table",
    dest_path: str,
    batch_size: int | str = 1024,
    stripe_size: int | str = 67108864,
    compression_block_size: int | str = 65536,
    row_index_stride: int | str = 10000,
    padding_tolerance: float | str = 0.0,
    dictionary_key_size_threshold: float | str = 0.0,
    bloom_filter_columns: list[str] | str | None = None,
    bloom_filter_fpp: float | str = 0.05,
    **kwargs: Any,
) -> None:
    import pyarrow.lib as pl
    import pyarrow.orc as po

    try:
        if bloom_filter_columns and isinstance(bloom_filter_columns, str):
            bloom_filter_columns = bloom_filter_columns.split(",")
        batch_size = int(batch_size)
        compression_block_size = int(compression_block_size)
        stripe_size = int(stripe_size)
        row_index_stride = int(row_index_stride)
        bloom_filter_fpp = float(bloom_filter_fpp)
        padding_tolerance = float(padding_tolerance)
        dictionary_key_size_threshold = float(dictionary_key_size_threshold)
    except (ValueError, TypeError, KeyError) as e:
        raise HarlequinCopyError(
            str(e),
            title=("Arrow raised an error when writing your data to an ORC file."),
        ) from e
    try:
        po.write_table(
            data,
            dest_path,
            batch_size=batch_size,
            compression_block_size=compression_block_size,
            stripe_size=stripe_size,
            row_index_stride=row_index_stride,
            bloom_filter_fpp=bloom_filter_fpp,
            padding_tolerance=padding_tolerance,
            dictionary_key_size_threshold=dictionary_key_size_threshold,
            bloom_filter_columns=bloom_filter_columns,  # type: ignore
            **kwargs,
        )
    except (pl.ArrowException, OSError, IOError, TypeError, ValueError) as e:
        raise HarlequinCopyError(
            str(e),
            title=("Arrow raised an error when writing your data to an ORC file."),
        ) from e


def _export_feather(
    data: "pa.Table",
    dest_path: str,
    compression: str | None = None,
    compression_level: str | int | None = None,
    chunksize: str | int | None = None,
    **kwargs: Any,
) -> None:
    import pyarrow.feather as pf
    import pyarrow.lib as pl

    try:
        compression_level = int(compression_level) if compression_level else None
        chunksize = int(chunksize) if chunksize else None
    except (ValueError, TypeError, KeyError) as e:
        raise HarlequinCopyError(
            str(e),
            title=("Arrow raised an error when writing your data to a Feather file."),
        ) from e

    try:
        pf.write_feather(
            data,
            dest_path,
            compression=compression,
            compression_level=compression_level,
            chunksize=chunksize,
            **kwargs,
        )
    except (pl.ArrowException, OSError, IOError, TypeError, ValueError) as e:
        raise HarlequinCopyError(
            str(e),
            title=("Arrow raised an error when writing your data to a Feather file."),
        ) from e


# `tsv` is `csv` with a separator; `jsonl` and `ndjson` are `json` without its
# enclosing array; `arrow` is what the feather writer's format is called
# everywhere except in pyarrow. None of them is a new writer.
_FILE_FORMATS: dict[str, _FileFormat] = {
    "csv": _FileFormat(exporter=_export_csv, suffix=".csv"),
    "tsv": _FileFormat(exporter=_export_csv, suffix=".tsv", defaults={"sep": "\t"}),
    "json": _FileFormat(
        exporter=_export_json, suffix=".json", defaults={"array": True}
    ),
    "jsonl": _FileFormat(
        exporter=_export_json, suffix=".jsonl", defaults={"array": False}
    ),
    "ndjson": _FileFormat(
        exporter=_export_json, suffix=".ndjson", defaults={"array": False}
    ),
    "parquet": _FileFormat(exporter=_export_parquet, suffix=".parquet"),
    "orc": _FileFormat(exporter=_export_orc, suffix=".orc"),
    "feather": _FileFormat(exporter=_export_feather, suffix=".feather"),
    "arrow": _FileFormat(exporter=_export_feather, suffix=".arrow"),
}
