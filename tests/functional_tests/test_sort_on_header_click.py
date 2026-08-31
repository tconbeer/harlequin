from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from rich.text import Text
from textual.coordinate import Coordinate
from textual.pilot import Pilot
from textual_fastdatatable import DataTable

from harlequin import Harlequin
from harlequin.components.results_viewer import (
    SORT_ASC_GLYPH,
    SORT_DESC_GLYPH,
    ResultsTable,
)

Waiter = Callable[[Harlequin], Awaitable[None]]


def visible_table(app: Harlequin) -> ResultsTable:
    table = app.results_viewer.get_visible_table()
    assert table is not None
    return table


def header_labels(app: Harlequin) -> list[str]:
    return [column.label.plain for column in visible_table(app).ordered_columns]


def column_values(app: Harlequin, index: int) -> list[object]:
    table = visible_table(app)
    return [table.get_cell_at(Coordinate(row, index)) for row in range(table.row_count)]


async def run_sql(
    app: Harlequin, pilot: Pilot[None], wait: Waiter, sql: str
) -> ResultsTable:
    assert app.editor is not None
    app.editor.text = sql
    await pilot.press("ctrl+j")
    for _ in range(3):
        await wait(app)
        await pilot.pause()
    await pilot.wait_for_scheduled_animations()
    return visible_table(app)


async def click_header(app: Harlequin, pilot: Pilot[None], index: int) -> None:
    table = visible_table(app)
    table.post_message(DataTable.HeaderSelected(table, index, label=Text()))
    await pilot.pause()
    await pilot.pause()


async def ready(app: Harlequin, pilot: Pilot[None], wait: Waiter) -> None:
    await wait(app)
    while app.editor is None:
        await pilot.pause()


THREE_ROWS = "select * from (values (3, 'c'), (1, 'a'), (2, 'b')) as t(a, b)"


@pytest.mark.asyncio
async def test_header_click_cycles_ascending_descending_unsorted(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Waiter,
) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        await ready(app, pilot, wait_for_workers)
        await run_sql(app, pilot, wait_for_workers, THREE_ROWS)
        assert column_values(app, 0) == [3, 1, 2]
        assert not any(
            g in "".join(header_labels(app)) for g in (SORT_ASC_GLYPH, SORT_DESC_GLYPH)
        )

        await click_header(app, pilot, 0)
        assert column_values(app, 0) == [1, 2, 3]
        assert column_values(app, 1) == ["a", "b", "c"]  # whole rows move
        assert header_labels(app)[0].endswith(SORT_ASC_GLYPH)
        assert SORT_ASC_GLYPH not in header_labels(app)[1]
        assert await app_snapshot(app, "sorted ascending")

        await click_header(app, pilot, 0)
        assert column_values(app, 0) == [3, 2, 1]
        assert header_labels(app)[0].endswith(SORT_DESC_GLYPH)

        # a third click restores the order the database returned
        await click_header(app, pilot, 0)
        assert column_values(app, 0) == [3, 1, 2]
        assert not any(
            g in "".join(header_labels(app)) for g in (SORT_ASC_GLYPH, SORT_DESC_GLYPH)
        )
        assert visible_table(app).sort_column is None


@pytest.mark.asyncio
async def test_clicking_another_header_starts_ascending(
    app: Harlequin, wait_for_workers: Waiter
) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        await ready(app, pilot, wait_for_workers)
        await run_sql(app, pilot, wait_for_workers, THREE_ROWS)
        await click_header(app, pilot, 0)
        await click_header(app, pilot, 0)
        assert column_values(app, 0) == [3, 2, 1]

        await click_header(app, pilot, 1)
        assert column_values(app, 1) == ["a", "b", "c"]
        labels = header_labels(app)
        assert SORT_DESC_GLYPH not in labels[0] and SORT_ASC_GLYPH not in labels[0]
        assert labels[1].endswith(SORT_ASC_GLYPH)


@pytest.mark.asyncio
async def test_sorting_a_limited_result_warns_once(
    app: Harlequin, wait_for_workers: Waiter
) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        await ready(app, pilot, wait_for_workers)
        bar = app.run_query_bar
        bar.limit_checkbox.value = True
        await pilot.pause()
        assert bar.limit_value == 500
        table = await run_sql(
            app, pilot, wait_for_workers, "select range as a from range(600)"
        )
        assert table.fetch_truncated
        assert table.row_count == 500

        await click_header(app, pilot, 0)
        await click_header(app, pilot, 0)
        assert column_values(app, 0)[0] == 499  # the largest of the fetched rows
        warnings = [
            n.message for n in app._notifications if "not the whole result" in n.message
        ]
        assert warnings == ["Sorted the 500 rows fetched, not the whole result."]
