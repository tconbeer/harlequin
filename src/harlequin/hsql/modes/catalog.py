"""`--catalog`: what is one level below `--path`, as rows.

A listing is rows -- `path`, `name`, `type`, `query_name` -- so it goes out
through the same writer a query's results do. That is what makes `--format csv`,
`-o PATH`, `-t`/`-A` and `--display-rows` mean here exactly what they mean
there, and it is why M2 adds no output subsystem at all: a second renderer for
the same job is how `--format json` and a catalog's JSON end up disagreeing
about how to spell a null.

**`path` is written to be passed back.** Each row's path is the one that lists
*that* item's children, quoted where a label needs it, so walking down a catalog
is copying a cell from the last answer rather than guessing at a spelling.
**`query_name` is written for the same reason**: the adapter has already worked
out whether this backend wants `"Orders"` or `` `orders` ``, and emitting it
means the agent never has to.

Describing a relation is listing it: `--path db.schema.orders` is the columns of
`orders`, with their types and their quoted identifiers, which is everything a
`describe` was going to print.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, BinaryIO, Mapping

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinConnection
    from harlequin.layout import LayoutOptions
    from harlequin.navigate import CatalogPath
    from harlequin.query import ResultSet

COLUMNS = ("path", "name", "type", "query_name")
"""The columns of a listing, whatever level it came from.

`type` is the catalog's short label -- `t`, `sch`, `##` -- which is what a level
tells you about itself; §1.2 of the M2 plan is why there is nothing else it
could say, since what a level *is* belongs to the adapter.
"""


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
    some of them, which is the one thing a listing cannot say for itself under
    `-t`.

    Raises: HarlequinCatalogPathError if the path names nothing, and whatever
    the adapter raises while fetching a level.
    """
    # deferred, both of them: the row machinery is pyarrow, and the walk is only
    # reachable from this mode. Nothing else in hsql should pay for either.
    from harlequin.hsql import output
    from harlequin.navigate import list_children, spell
    from harlequin.query import rows_to_result

    listing = list_children(connection, path)
    # in the order the adapter reported them, and not sorted: a relation's
    # children are its columns, whose order is the table's rather than the
    # alphabet's, and sorting them would throw away the one thing that order
    # carries.
    rows = [
        (
            spell([*path.segments, item.label]),
            item.label,
            item.type_label,
            item.query_name,
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
