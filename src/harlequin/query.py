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
from dataclasses import dataclass, field
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
    """How many rows to ask the database for, and whether to detect more.

    This is the *hard* limit, in both front ends: `cursor.set_limit()`, so fewer
    rows leave the database. The soft caps -- how many rows the Results Viewer
    holds, how many a text layout prints -- are applied downstream of this, over
    rows that have already been fetched, and are `display_limit` here and
    `LayoutOptions.max_rows` there.

    `detect_overflow` is what makes a hard limit's truncation knowable:
    `set_limit(n)` followed by `fetchall()` returns at most n rows and says
    nothing about whether an n+1th existed, so exactly n is ambiguous. Asking
    for one row more than we intend to keep settles it, and costs one row.
    """

    max_rows: int | None = None
    """None means unlimited. 0 fetches no rows, which is a header and no data."""

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
    """Whether the database had more rows than were fetched.

    Only ever true under a hard limit with `detect_overflow`; a soft cap keeps
    fewer rows than were fetched and leaves the total known exactly.
    """

    fetched_row_count: int
    """How many rows the database returned, the overflow probe row excluded.

    Exact when `truncated` is false, and the hard limit when it is true. Not
    `backend.source_row_count`, which counts the probe row that only exists to
    prove there was one.
    """

    elapsed: float
    """Seconds spent fetching, not including execution."""

    editable_columns: dict[int, tuple[str, str, bool]] = field(default_factory=dict)
    """Result-column index -> (table, column, is primary key), for the columns
    the cursor reports as read straight from a table; see
    `HarlequinCursor.editable_columns()`."""

    @property
    def row_count(self) -> int:
        """How many rows this result set holds, after any soft cap."""
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

        Duplicate names have already been made unique here, by the backend;
        `export.write_file()` de-duplicates the same way, so a query exports the
        same header whichever of them saw it first.
        """
        return self.backend.data

    def text_columns(self) -> pa.Table:
        """Every value in this result set, `CAST(... AS VARCHAR)`.

        The text layouts need strings, and this is where they get them: through
        duckdb, the same serializer `export.write_file()` writes csv and json
        with. Deriving them any other way -- `str()`, or the data table's
        display formatter -- would make `--format table` and `--format csv`
        disagree about what a timestamp, a decimal or a blob looks like, and
        the display formatter would additionally render `1234567` as
        `1,234,567` under a locale that groups digits.

        SQL `NULL` comes back as Python `None`, so a null stays distinguishable
        from the literal string a caller chose to render nulls as.
        """
        data = self.arrow_table()
        if data.num_columns == 0:
            return data
        if all(_is_text(column.type) for column in data.columns):
            # Already text, and casting it would be a database driver imported
            # to turn a string into itself. This is the path a listing takes --
            # `hsql --config list-profiles` and, later, `--catalog` -- where the
            # rows never came from a database and duckdb has no other reason to
            # be here. It is a small win for an all-VARCHAR `select`, too.
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


def _is_text(data_type: pa.DataType) -> bool:
    """Whether a column is already the strings a text layout wants.

    Strings only: an all-null column is `None` before the cast and after it,
    but duckdb still gives it a type, and `text_columns()` promises every
    column it returns is one. `rows_to_result()` builds its columns as strings
    for exactly this reason, so a listing never arrives here untyped.
    """
    return pa.types.is_string(data_type) or pa.types.is_large_string(data_type)


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


def fetch(
    executed: ExecutedStatement,
    limit: RowLimit | None = None,
    display_limit: int | None = None,
) -> ResultSet:
    """Drain one executed statement's cursor into a `ResultSet`.

    `limit` is the hard limit `execute()` was given, and is passed here too so
    that the extra row `detect_overflow` fetched is dropped rather than kept.
    `display_limit` is a *soft* cap on top of it, for a caller that fetched more
    rows than it intends to show: the result set holds that many, and still
    knows exactly how many the database returned.

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

    kept = [n for n in (limit.max_rows, display_limit) if n is not None]
    # `data` may be None, an empty sequence, or rows with no names on them --
    # none of which carry the columns the cursor just described. Handing those
    # names to `create_backend()` is what makes a result with no rows an empty
    # table with a header.
    backend = create_backend(
        data,
        max_rows=min(kept) if kept else None,
        column_names=[name for name, _ in columns],
    )

    truncated = (
        limit.detect_overflow
        and limit.max_rows is not None
        and backend.source_row_count > limit.max_rows
    )

    return ResultSet(
        statement=executed.statement,
        columns=columns,
        backend=backend,
        truncated=truncated,
        fetched_row_count=backend.source_row_count - (1 if truncated else 0),
        elapsed=elapsed,
    )


def rows_to_result(
    columns: Sequence[str], rows: Sequence[Sequence[str | None]]
) -> ResultSet:
    """Rows that never came from a database, as a result set.

    hsql's modes produce rows too -- the profiles `--config list-profiles`
    found, and later a level of the catalog -- and a row is a row. Building the
    same `ResultSet` the query path builds means they inherit every `--format`,
    the psql switches, `-o PATH` and the byte-for-byte determinism the snapshots
    pin, instead of a second renderer that would sooner or later disagree with
    the first about how to spell a null.

    Every value is text already, which is what keeps `text_columns()` from
    casting a listing through duckdb.
    """
    # typed rather than inferred: a column of nothing but nulls would be an
    # Arrow null column, and `text_columns()` would send the whole listing
    # through duckdb to give it the type it could have been built with.
    values = list(zip(*rows, strict=True)) if rows else [()] * len(columns)
    table = pa.table(
        [pa.array(column, type=pa.string()) for column in values],
        names=list(columns),
    )
    return ResultSet(
        statement=Statement(sql="", index=0),
        # `s` is the short type label an adapter gives a string column, and the
        # only thing this data is. It reaches `--stats`, and nothing else.
        columns=[(name, "s") for name in columns],
        backend=create_backend(table),
        # a listing is whole: there is no database that held more of it.
        truncated=False,
        fetched_row_count=len(rows),
        elapsed=0.0,
    )
