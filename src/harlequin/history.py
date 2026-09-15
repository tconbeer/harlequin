"""The queries the History screen shows, read from `harlequin.query_log`."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.style import StyleType
from rich.text import Text

from harlequin.catalog_cache import load_legacy_history, restrict_legacy_cache
from harlequin.query_log import (
    READ_COLUMNS,
    UI_BUSY_TIMEOUT_MS,
    Record,
    Status,
    adopt,
    default_path,
    recent,
)

DEFAULT_ROWS = 500
"""How many records a screen reads when it is not told otherwise."""

ERROR_STYLE: StyleType = "bold italic red"
"""What a record renders an unsuccessful run in, for a caller with no theme."""


@dataclass
class QueryExecution:
    """One logged statement, as the History screen shows it.

    A version-2 catalog cache pickles this class and `History` by module path,
    so renaming or moving either one stops those files loading.
    """

    query_text: str
    executed_at: datetime
    result_row_count: int | None
    elapsed: float | None
    status: Status = "ok"

    def render(self, error_style: StyleType = ERROR_STYLE) -> RenderableType:
        """The record as a row: when it ran, how it went, and the query."""
        ts = self.executed_at.strftime("%a, %b %d %H:%M:%S")
        if self.status != "ok":
            result = Text(self.status.upper(), style=error_style, justify="right")
        else:
            res = (
                f"{self.result_row_count:n} "
                f"{'record' if self.result_row_count == 1 else 'records'}"
                if self.result_row_count
                else "SUCCESS"
            )
            # a statement whose rows were never counted -- DDL, or a session
            # that died mid-fetch -- has no duration to report either
            result = (
                Text(res, style="bold", justify="right")
                if self.elapsed is None
                else Text.assemble(
                    (res, "bold"),
                    " in ",
                    (f"{self.elapsed:.2f}s", "bold"),
                    justify="right",
                )
            )
        query_lines = self.query_text.strip().splitlines()
        if len(query_lines) > 8:
            continuation: RenderableType = Text(
                f"… ({len(query_lines) - 7} more lines)\n", style="italic"
            )
            query_lines = query_lines[0:7]
        else:
            continuation = ""

        return Group(
            Columns(
                renderables=[Text(ts, style="bold"), result],
                expand=True,
            ),
            "\n".join(query_lines),
            continuation,
        )

    def __rich__(self) -> RenderableType:
        return self.render()


@dataclass
class History:
    """What one connection has run, newest first.

    Pickled by name in a version-2 catalog cache; see `QueryExecution`.
    """

    queries: Sequence[QueryExecution]

    def __iter__(self) -> Iterator[QueryExecution]:
        return iter(self.queries)

    def __len__(self) -> int:
        return len(self.queries)

    @classmethod
    def recent(
        cls,
        *,
        connection: str | None = None,
        limit: int | None = DEFAULT_ROWS,
        search: str | None = None,
    ) -> "History":
        """The newest logged queries, newest first.

        Waits only briefly for a lock another process holds, so that a worker
        is not held up by someone else's write.

        Raises: sqlite3.Error, for a store that is there and cannot be read.
        """
        rows = recent(
            connection=connection,
            search=search,
            limit=limit,
            busy_timeout_ms=UI_BUSY_TIMEOUT_MS,
        )
        records = (_record(row) for row in rows)
        return cls(queries=[record for record in records if record is not None])


def _record(row: Sequence[Any]) -> QueryExecution | None:
    """One row of the store as a record, or None for a row that cannot be read.

    The store is a file on the user's disk, so a row something else wrote badly
    costs that row rather than the screen.
    """
    values = dict(zip(READ_COLUMNS, row, strict=True))
    try:
        # kept in UTC and read in the reader's own time zone, which is the one
        # they ran the query in
        executed_at = datetime.fromisoformat(str(values["run_at"])).astimezone()
        elapsed_ms = values["elapsed_ms"]
        elapsed = None if elapsed_ms is None else float(elapsed_ms) / 1000
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    return QueryExecution(
        query_text=str(values["sql"]),
        executed_at=executed_at,
        result_row_count=values["rows"],
        elapsed=elapsed,
        status=values["status"],
    )


_migrated: set[tuple[str, Path]] = set()
_migrated_lock = threading.Lock()
"""The connections this process has already moved out of a version-2 cache."""


def migrate_pickled_history(connection: str | None) -> int:
    """Move a version-2 cache's queries for one connection into the store.

    Returns how many were moved. The store's own marker is what makes the move
    happen once; this set only keeps a session from re-reading the pickle.

    Raises: sqlite3.Error, OSError.
    """
    if not connection:
        return 0
    store = default_path()
    with _migrated_lock:
        if (connection, store) in _migrated:
            return 0
    moved = 0
    records = load_legacy_history(connection)
    if records is not None:
        adopted = adopt(
            connection, [_stored(record) for record in records], program="harlequin"
        )
        if adopted is not None:
            restrict_legacy_cache()
            moved = adopted
    with _migrated_lock:
        _migrated.add((connection, store))
    return moved


def _stored(record: QueryExecution) -> Record:
    """One version-2 record as the store holds it: the inverse of `_record`."""
    # a version-2 record had no status: a negative row count is how it said the
    # query failed
    failed = (record.result_row_count or 0) < 0
    return Record(
        sql=record.query_text,
        run_at=record.executed_at.astimezone(timezone.utc),
        status="error" if failed else "ok",
        rows=None if failed else record.result_row_count,
        elapsed_ms=None if record.elapsed is None else record.elapsed * 1000,
    )
