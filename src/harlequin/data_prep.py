from __future__ import annotations

from typing import TYPE_CHECKING

from textual_fastdatatable.backend import ArrowBackend

if TYPE_CHECKING:
    import pyarrow as pa

    from harlequin.components.results_viewer import ResultsTable


def prepare_data(table: "ResultsTable") -> "pa.Table":
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
        return table.backend.source_data.rename_columns(export_names)
    return table.backend.source_data
