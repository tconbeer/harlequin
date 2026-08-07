"""Executing SQL and collecting its results, without a UI.

This is the core both front ends run queries through. It keeps the two-phase
shape the Textual app has always had -- execute every statement, *then* fetch
every result -- rather than flattening it, because that split is what lets the
app report "query executed" before any data materializes.

Results are normalized by `textual_fastdatatable.create_backend()`, the same
call the app's results viewer makes. A second normalizer would put the most
drift-prone question in the codebase (what counts as a row, what counts as
null, how a nested struct comes out) in two places, and the disagreement would
surface as two front ends showing different data for the same query.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, Literal, Sequence

from textual_fastdatatable.backend import create_backend

from harlequin.statements import Statement

if TYPE_CHECKING:
    from textual_fastdatatable.backend import DataTableBackend

    from harlequin.adapter import HarlequinConnection, HarlequinCursor

OnError = Literal["stop", "continue"]


@dataclass(frozen=True)
class RowLimit:
    """How many rows to ask for, and whether to detect that there were more.

    Harlequin has two different limits and they are not interchangeable. The
    app's `--limit` is a *soft* display cap: it fetches everything and caps what
    is loaded into the viewer, which is why it can report an exact total. A
    headless caller wants the *hard* one -- fewer rows leave the database --
    and so can never learn the true total.

    That is what `detect_overflow` is for: `set_limit(n)` followed by
    `fetchall()` returns at most n rows and says nothing about whether an n+1th
    existed, so exactly n is ambiguous. Asking for one row more than we intend
    to keep is what makes truncation knowable, and it costs exactly one row.
    """

    max_rows: int | None = None
    """None means unlimited."""

    detect_overflow: bool = False

    @property
    def fetch_limit(self) -> int | None:
        """The limit to hand `HarlequinCursor.set_limit()`."""
        if self.max_rows is None:
            return None
        return self.max_rows + 1 if self.detect_overflow else self.max_rows


@dataclass
class ExecutedStatement:
    """One statement, after the database has been asked to run it.

    Exactly one of `cursor` and `error` is meaningful: a `cursor` for a query
    with a result set, an `error` if the database refused it, and neither for
    DDL/DML, which returns no cursor and raises nothing.
    """

    statement: Statement
    cursor: HarlequinCursor | None = None
    error: BaseException | None = None

    @property
    def has_result_set(self) -> bool:
        return self.cursor is not None


@dataclass
class ResultSet:
    """The data a single statement returned."""

    statement: Statement
    columns: list[tuple[str, str]]
    """(name, short type) pairs, in the order the data is returned."""

    backend: DataTableBackend | None
    """None when the statement returned no rows at all."""

    truncated: bool
    """Whether the database had more rows than this result set holds."""

    elapsed: float
    """Seconds spent fetching, not including execution."""

    @property
    def row_count(self) -> int:
        return 0 if self.backend is None else self.backend.row_count

    def rows(self) -> Iterator[Sequence[Any]]:
        if self.backend is None:
            return
        for i in range(self.backend.row_count):
            yield self.backend.get_row_at(i)


def execute(
    connection: HarlequinConnection,
    statements: Sequence[Statement],
    limit: RowLimit | None = None,
    on_error: OnError = "stop",
) -> Iterator[ExecutedStatement]:
    """Run each statement, yielding one `ExecutedStatement` per statement.

    Yields lazily, so a caller that stops consuming stops the script. Nothing
    is fetched here; pass each yielded statement to `fetch()` for that.

    A statement the database refuses is yielded with its `error` set rather
    than raising, so the caller keeps the statement it belongs to. Under the
    default `on_error="stop"` no further statement is run; under `"continue"`
    the script runs to the end. `KeyboardInterrupt` is not an error in this
    sense and propagates.
    """
    limit = limit if limit is not None else RowLimit()
    fetch_limit = limit.fetch_limit
    for statement in statements:
        try:
            cursor = connection.execute(statement.sql)
        # adapters are supposed to raise HarlequinQueryError, but a raw driver
        # exception must not take down the caller.
        except Exception as e:
            yield ExecutedStatement(statement=statement, error=e)
            if on_error == "stop":
                return
        else:
            if cursor is not None and fetch_limit is not None:
                cursor = cursor.set_limit(fetch_limit)
            yield ExecutedStatement(statement=statement, cursor=cursor)


def fetch(executed: ExecutedStatement, limit: RowLimit | None = None) -> ResultSet:
    """Drain one executed statement's cursor into a `ResultSet`.

    `limit.max_rows` caps the rows the result set holds, which is not
    necessarily the number fetched: under `detect_overflow` `execute()` asked
    for one more than this, and the extra row is what `truncated` is inferred
    from.

    Raises whatever the adapter raises; a caller that runs several statements
    decides for itself whether one failure ends the batch.
    """
    if executed.cursor is None:
        raise ValueError(
            f"Statement {executed.statement.index} has no cursor to fetch from."
        )

    limit = limit if limit is not None else RowLimit()
    started_at = time.monotonic()
    data = executed.cursor.fetchall()
    columns = executed.cursor.columns()
    elapsed = time.monotonic() - started_at

    # a cursor with no rows returns None, which is not a shape `create_backend`
    # can normalize -- and there is nothing to normalize.
    backend = None if data is None else create_backend(data, max_rows=limit.max_rows)

    return ResultSet(
        statement=executed.statement,
        columns=columns,
        backend=backend,
        truncated=(
            backend is not None and backend.source_row_count > backend.row_count
        ),
        elapsed=elapsed,
    )
