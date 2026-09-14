"""The queries the History screen shows, read from the query log.

One indexed read of `harlequin.query_log` per screen, so a human scrolling
their history sees what `hsql` ran against the same database as well as what
they ran themselves, and a session that never quit cleanly keeps both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.style import StyleType
from rich.text import Text

from harlequin.query_log import READ_COLUMNS, UI_BUSY_TIMEOUT_MS, Status, recent

DEFAULT_ROWS = 500
"""How many records a screen reads when it is not told otherwise."""

ERROR_STYLE: StyleType = "bold italic red"
"""What a record renders an unsuccessful run in, for a caller with no theme."""


@dataclass
class QueryExecution:
    """One logged statement, as the History screen shows it."""

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
    """What one connection has run, newest first."""

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
        n: int | None = DEFAULT_ROWS,
        search: str | None = None,
        path: Path | None = None,
    ) -> "History":
        """The newest `n` logged queries, newest first.

        Waits only briefly for a lock another process holds: a missed screen
        beats a worker stalled behind someone else's write.

        Raises: sqlite3.Error, for a store that is there and cannot be read.
        """
        rows = recent(
            connection=connection,
            search=search,
            limit=n,
            path=path,
            busy_timeout_ms=UI_BUSY_TIMEOUT_MS,
        )
        records = (_record(row) for row in rows)
        return cls(queries=[record for record in records if record is not None])


def _record(row: Sequence[Any]) -> QueryExecution | None:
    """One row of the store as a record, or None for a row nothing can read.

    The store is a file on the user's disk, and one unreadable timestamp in it
    should cost that row rather than the screen.
    """
    values = dict(zip(READ_COLUMNS, row, strict=True))
    try:
        # kept in UTC and read in the reader's own time zone, which is the one
        # they ran the query in
        executed_at = datetime.fromisoformat(str(values["run_at"])).astimezone()
    except ValueError:
        return None
    elapsed_ms = values["elapsed_ms"]
    return QueryExecution(
        query_text=str(values["sql"]),
        executed_at=executed_at,
        result_row_count=values["rows"],
        elapsed=None if elapsed_ms is None else float(elapsed_ms) / 1000,
        status=values["status"],
    )
