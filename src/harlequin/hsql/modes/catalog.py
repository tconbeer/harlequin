"""Implements `hsql --catalog`: one level of the catalog, as rows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, BinaryIO, Mapping

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinConnection
    from harlequin.layout import LayoutOptions
    from harlequin.navigate import CatalogPath
    from harlequin.query import ResultSet

COLUMNS = ("path", "name", "query_name", "type", "type_label")
"""Both type columns: `type` is the database's own name for what this object is,
and `type_label` is the short label, which an adapter always populates."""


def report(
    out: BinaryIO,
    *,
    connection: "HarlequinConnection",
    path: "CatalogPath",
    format_name: str,
    layout_options: "LayoutOptions",
    file_options: Mapping[str, Any],
) -> "ResultSet":
    """Write one level of the catalog, and return the rows it wrote.

    The rows come back so the caller can say on stderr that a row cap dropped
    some of them, which a listing cannot say for itself under `-t`.

    Raises: HarlequinCatalogPathError if the path names nothing, and whatever
    the adapter raises while fetching a level.
    """
    # deferred, both of them: the row machinery is pyarrow, and the walk is only
    # reachable from this mode.
    from harlequin.hsql import output
    from harlequin.navigate import list_children, spell
    from harlequin.query import rows_to_result

    listing = list_children(connection, path)
    # not sorted, to preserve ordinal ordering for columns
    rows = [
        (
            spell([*path.segments, item.label]),
            item.label,
            item.query_name,
            item.type_name,
            item.type_label,
        )
        for item in listing.items
    ]
    result = rows_to_result(COLUMNS, rows)
    output.write(
        result,
        format_name,
        out,
        layout_options=layout_options,
        file_options=file_options,
    )
    return result
