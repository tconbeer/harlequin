from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from textual_fastdatatable.backend import ArrowBackend

from harlequin.exception import HarlequinCopyError

if TYPE_CHECKING:
    import pyarrow as pa

    from harlequin.components.results_viewer import ResultsTable


class ExporterCallable(Protocol):
    def __call__(self, data: "pa.Table", dest_path: str, **kwargs: Any) -> None: ...


def copy(
    table: "ResultsTable", path: Path, format_name: str, options: dict[str, Any]
) -> None:
    if table.row_count == 0:
        raise HarlequinCopyError("Cannot export empty table.")

    assert isinstance(table.backend, ArrowBackend)
    if table.plain_column_labels:
        # Arrow allows duplicate field names, but DuckDB will typically throw an error
        # when trying to export CSV, JSON, or PQ files with those dupe field names.
        export_names: list[str] = []
        for label in table.plain_column_labels:
            export_label = label
            n = 0
            while export_label in export_names:
                export_label = f"{label}_{n}"
                n += 1
            export_names.append(export_label)
        data = table.backend.source_data.rename_columns(export_names)
    else:
        data = table.backend.source_data

    dest_path = str(path.expanduser())
    # only include options that aren't None/Empty
    kwargs = {k: v for k, v in options.items() if v}
    exporters: dict[str, ExporterCallable] = {
        "csv": _export_csv,
        "parquet": _export_parquet,
        "json": _export_json,
        "orc": _export_orc,
        "feather": _export_feather,
    }
    exporters[format_name](data, dest_path, **kwargs)


def _export_csv(
    data: "pa.Table",
    dest_path: str,
    **kwargs: Any,
) -> None:
    import duckdb

    if kwargs.get("quoting"):
        kwargs["quoting"] = "ALL"
    if "header" not in kwargs:
        kwargs["header"] = False
    try:
        relation = duckdb.arrow(data)
        relation.write_csv(file_name=dest_path, **kwargs)
    except (duckdb.Error, OSError) as e:
        raise HarlequinCopyError(
            str(e),
            title=("DuckDB raised an error when writing your query to a CSV file."),
        ) from e


def _export_parquet(
    data: "pa.Table",
    dest_path: str,
    **kwargs: Any,
) -> None:
    import duckdb

    try:
        relation = duckdb.arrow(data)
        relation.write_parquet(
            file_name=dest_path, compression=kwargs.get("compression")
        )
    except (duckdb.Error, OSError) as e:
        raise HarlequinCopyError(
            str(e),
            title=("DuckDB raised an error when writing your query to a Parquet file."),
        ) from e


def _export_json(
    data: "pa.Table",
    dest_path: str,
    **kwargs: Any,
) -> None:
    import duckdb

    array = f"{', ARRAY TRUE' if kwargs.get('array') else ''}"
    compression = f", COMPRESSION {kwargs.get('compression')}"
    date_format = (
        f", DATEFORMAT '{kwargs.get('''date_format''')}'"
        if kwargs.get("date_format")
        else ""
    )
    ts_format = (
        f", TIMESTAMPFORMAT '{kwargs.get('''options.timestamp_format''')}'"
        if kwargs.get("options.timestamp_format")
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
    except (pl.ArrowException, OSError, IOError, TypeError) as e:
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
    except (pl.ArrowException, OSError, IOError, TypeError) as e:
        raise HarlequinCopyError(
            str(e),
            title=("Arrow raised an error when writing your data to a Feather file."),
        ) from e
