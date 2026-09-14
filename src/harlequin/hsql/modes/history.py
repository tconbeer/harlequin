"""Implements `hsql --history` and `--history-search`: the query log, as rows.

The one mode that reads a database without connecting to one: the store is a
local SQLite file both commands write as they run, and the only thing the
invocation's own adapter is asked for is the id that narrows the read to one
connection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, BinaryIO, Mapping, Sequence

if TYPE_CHECKING:
    from harlequin.layout import LayoutOptions
    from harlequin.query import ResultSet

COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_at", "s"),
    ("program", "s"),
    ("profile", "s"),
    ("adapter", "s"),
    ("status", "s"),
    ("rows", "##"),
    ("elapsed_ms", "#.#"),
    ("sql", "s"),
)
"""Each column the store returns, with the short type label it carries.

The names and their order are `query_log.READ_COLUMNS`; the labels are here
because nothing infers one from a value, and they are the ones both bundled
adapters give an int64 and a double.
"""

SQL_COLUMN = "sql"


def folded(sql: str) -> str:
    """One query on one line, which is the only shape a listing has.

    `layout.py` pads by terminal cells and has no concept of a cell spanning
    rows, and `--format table` and `--format csv` agree cell for cell -- so a
    column that is verbatim in one and folded in the other is not on offer.
    Folding loses the formatting and not the query: it still runs.
    """
    return " ".join(sql.split())


def report(
    out: BinaryIO,
    *,
    connection: str | None,
    search: str | None,
    limit: int | None,
    format_name: str,
    layout_options: "LayoutOptions",
    file_options: Mapping[str, Any],
) -> "ResultSet":
    """Write the newest logged queries first, and return the rows it wrote.

    The rows come back so the caller can say on stderr that a row cap dropped
    some of them, which a listing cannot say for itself under `-t`.

    Raises: sqlite3.Error, for a store that is there and cannot be read.
    """
    # deferred, both of them: the row machinery is pyarrow, and the store is
    # only read from this mode.
    from harlequin.hsql import output
    from harlequin.query import typed_rows_to_result
    from harlequin.query_log import recent

    # one row more than we keep is what makes truncation knowable, the same
    # probe a hard limit on a query fetches
    rows = recent(
        connection=connection,
        search=search,
        limit=None if limit is None else limit + 1,
    )
    truncated = limit is not None and len(rows) > limit
    kept = rows[:limit] if truncated else rows
    result = typed_rows_to_result(COLUMNS, _folded(kept), truncated=truncated)
    output.write(
        result,
        format_name,
        out,
        layout_options=layout_options,
        file_options=file_options,
    )
    return result


def _folded(rows: Sequence[Sequence[Any]]) -> list[tuple[Any, ...]]:
    """Every row with its `sql` on one line."""
    position = [name for name, _ in COLUMNS].index(SQL_COLUMN)
    return [
        (*row[:position], folded(row[position]), *row[position + 1 :]) for row in rows
    ]
