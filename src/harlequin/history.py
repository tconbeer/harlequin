from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.text import Text


@dataclass
class QueryExecution:
    query_text: str
    executed_at: datetime
    result_row_count: int
    elapsed: float

    def __rich__(self) -> RenderableType:
        ts = self.executed_at.strftime("%a, %b %d %H:%M:%S")
        if self.result_row_count < 0:
            result = Text("ERROR", style="bold italic red", justify="right")
        else:
            res = (
                f"{self.result_row_count:n} "
                f"{'record' if self.result_row_count == 1 else 'records'}"
                if self.result_row_count
                else "SUCCESS"
            )
            elapsed = f"{self.elapsed:.2f}s"
            result = Text.assemble(
                (res, "bold"), " in ", (elapsed, "bold"), justify="right"
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


@dataclass
class History:
    queries: deque[QueryExecution]

    def __iter__(self) -> Iterator[QueryExecution]:
        return iter(self.queries)

    def append(self, query_text: str, result_row_count: int, elapsed: float) -> None:
        self.queries.append(
            QueryExecution(
                query_text=query_text.strip(),
                executed_at=datetime.now(),
                result_row_count=result_row_count,
                elapsed=elapsed,
            )
        )

    @classmethod
    def blank(cls) -> "History":
        return cls(queries=deque([], maxlen=500))
