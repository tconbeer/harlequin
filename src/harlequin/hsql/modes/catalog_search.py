"""Implements `hsql --catalog-search`: the objects whose name matches a term.

The question a walk cannot answer -- *where does `orders` live*, *which tables
have a `customer_id`* -- so it is an optional capability rather than a
recursive listing: one introspection query where the adapter can serve it, and
a refusal naming the adapter where it cannot. Every level the catalog has, so
that a term matching nothing means nothing is named that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, BinaryIO, Mapping

from harlequin.hsql.modes.catalog import COLUMNS, rows

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinConnection
    from harlequin.layout import LayoutOptions
    from harlequin.navigate import CatalogPath
    from harlequin.query import ResultSet


def report(
    out: BinaryIO,
    *,
    connection: "HarlequinConnection",
    term: str,
    path: "CatalogPath",
    format_name: str,
    layout_options: "LayoutOptions",
    file_options: Mapping[str, Any],
) -> "ResultSet":
    """Write what the search found, and return the rows it wrote.

    The rows come back so the caller can say on stderr that a row cap dropped
    some of them, which a listing cannot say for itself under `-t`.

    Raises: HarlequinCatalogPathError if `path` names nothing, and whatever the
    adapter raises while searching.
    """
    # deferred, both of them: the row machinery is pyarrow, and the search is
    # only reachable from this mode.
    from harlequin.hsql import output
    from harlequin.navigate import search
    from harlequin.query import rows_to_result

    # every kind of item, and not sorted: the adapter's order is the one its
    # own query chose
    found = search(connection, term, path)
    result = rows_to_result(
        COLUMNS, rows([(match.parents, match.item) for match in found])
    )
    output.write(
        result,
        format_name,
        out,
        layout_options=layout_options,
        file_options=file_options,
    )
    return result
