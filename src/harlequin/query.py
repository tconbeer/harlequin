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
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, Literal, Sequence

import pyarrow as pa
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

    backend: DataTableBackend[pa.Table]
    """Always present, even for a statement that returned no rows at all.

    `create_backend()` is given the columns the cursor described, so a result
    with no rows is an empty table with a header rather than nothing.
    """

    truncated: bool
    """Whether the database had more rows than this result set holds."""

    elapsed: float
    """Seconds spent fetching, not including execution."""

    @property
    def row_count(self) -> int:
        return self.backend.row_count

    # Deliberately no row iterator. Every consumer of a result set works
    # columnwise -- the file formats hand `backend.source_data` to duckdb or
    # pyarrow, and the text layouts read a VARCHAR-cast Arrow table -- so a
    # row-at-a-time accessor would be a slow path with no callers. Reach for
    # `backend.source_data` instead; `get_row_at()` costs an Arrow slice and a
    # dict per row (1.8s vs 0.34s over 100k rows) and materializes the whole
    # result as Python objects, which is what `--limit` exists to avoid.

    def arrow_table(self) -> pa.Table:
        """The rows this result set holds, as Arrow, under the cursor's names.

        The rows kept, not the rows fetched: under `detect_overflow` the extra
        row that made `truncated` knowable is not in it. (The app's export
        dialog wants all of `source_data` -- it fetched everything on purpose.)

        Sliced from `source_data` rather than read from `backend.data`, which is
        otherwise the same rows: `data` has unique column names, because
        `to_pylist()` silently drops duplicate-named fields, and `source_data`
        has the ones the cursor reported. Names have to survive to
        `export.write_file()`, which is the one place that decides what a
        duplicate becomes -- otherwise `select 1 as a, 2 as a` exports a
        different header here than it does from the app.
        """
        return self.backend.source_data.slice(0, self.backend.row_count)

    def text_columns(self) -> pa.Table:
        """Every value in this result set, `CAST(... AS VARCHAR)`.

        The text layouts need strings, and this is where they get them: through
        duckdb, the same serializer `export.write_file()` writes csv and json
        with. Deriving them any other way -- `str()`, or the data table's
        display formatter -- would make `-F table` and `-F csv` disagree about
        what a timestamp, a decimal or a blob looks like, and the display
        formatter would additionally render `1234567` as `1,234,567` under a
        locale that groups digits.

        SQL `NULL` comes back as Python `None`, so a null stays distinguishable
        from the literal string a caller chose to render nulls as.
        """
        data = self.arrow_table()
        if data.num_columns == 0:
            return data
        try:
            return _cast_to_text(data)
        except Exception as e:  # noqa: BLE001
            # not an expected path: duckdb ingests every Arrow type these
            # adapters have been seen to produce. Belt and braces, and loud,
            # because the output is no longer the output a file export would
            # have produced.
            warnings.warn(
                f"Could not serialize this result set with duckdb ({e}); "
                "falling back to Python's str(), which may render some values "
                "differently than an exported file would.",
                RuntimeWarning,
                stacklevel=2,
            )
            return _cast_to_text_with_str(data)


def _cast_to_text(data: pa.Table) -> pa.Table:
    """Cast every column to VARCHAR, in duckdb."""
    import duckdb

    names = data.column_names
    # duckdb refuses an Arrow table with duplicate field names, so it is handed
    # positional ones and the projection aliases the originals back on.
    positional = [f"c{i}" for i in range(len(names))]
    relation = duckdb.from_arrow(data.rename_columns(positional))
    # `duckdb.typing.VARCHAR` was removed in 1.5; `sqltype` spans 1.1 to 1.5.
    varchar = duckdb.sqltype("VARCHAR")
    projected = relation.project(
        *[
            duckdb.ColumnExpression(position).cast(varchar).alias(name)
            for position, name in zip(positional, names, strict=False)
        ]
    ).arrow()
    # duckdb >= 1.3 hands back a RecordBatchReader rather than a table.
    return projected if isinstance(projected, pa.Table) else projected.read_all()


def _cast_to_text_with_str(data: pa.Table) -> pa.Table:
    return pa.Table.from_arrays(
        [
            pa.array(
                [None if value is None else str(value) for value in column.to_pylist()],
                type=pa.string(),
            )
            for column in data.columns
        ],
        names=data.column_names,
    )


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

    # `data` may be None, an empty sequence, or rows with no names on them --
    # none of which carry the columns the cursor just described. Handing those
    # names to `create_backend()` is what makes a result with no rows an empty
    # table with a header.
    backend = create_backend(
        data,
        max_rows=limit.max_rows,
        column_names=[name for name, _ in columns],
    )

    return ResultSet(
        statement=executed.statement,
        columns=columns,
        backend=backend,
        truncated=backend.source_row_count > backend.row_count,
        elapsed=elapsed,
    )
