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


async def click_header(
    app: Harlequin, pilot: Pilot[None], wait: Waiter, index: int
) -> None:
    """Clicks a header and waits for the re-run it causes to land."""
    table = visible_table(app)
    table.post_message(DataTable.HeaderSelected(table, index, label=Text()))
    for _ in range(3):
        await wait(app)
        await pilot.pause()
    await pilot.wait_for_scheduled_animations()


def editor_text(app: Harlequin) -> str:
    assert app.editor is not None
    return app.editor.text


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

        await click_header(app, pilot, wait_for_workers, 0)
        assert column_values(app, 0) == [1, 2, 3]
        assert column_values(app, 1) == ["a", "b", "c"]  # whole rows move
        assert editor_text(app) == f'{THREE_ROWS}\norder by "a" asc'
        assert header_labels(app)[0].endswith(SORT_ASC_GLYPH)
        assert SORT_ASC_GLYPH not in header_labels(app)[1]
        assert await app_snapshot(app, "sorted ascending")

        await click_header(app, pilot, wait_for_workers, 0)
        assert column_values(app, 0) == [3, 2, 1]
        assert header_labels(app)[0].endswith(SORT_DESC_GLYPH)
        assert (
            editor_text(app) == f'{THREE_ROWS}\norder by "a" desc'
        )  # replaced, not stacked

        # a third click puts the statement back as written and re-runs it
        await click_header(app, pilot, wait_for_workers, 0)
        assert column_values(app, 0) == [3, 1, 2]
        assert editor_text(app) == THREE_ROWS
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
        await click_header(app, pilot, wait_for_workers, 0)
        await click_header(app, pilot, wait_for_workers, 0)
        assert column_values(app, 0) == [3, 2, 1]

        await click_header(app, pilot, wait_for_workers, 1)
        assert column_values(app, 1) == ["a", "b", "c"]
        assert editor_text(app) == f'{THREE_ROWS}\norder by "b" asc'
        labels = header_labels(app)
        assert SORT_DESC_GLYPH not in labels[0] and SORT_ASC_GLYPH not in labels[0]
        assert labels[1].endswith(SORT_ASC_GLYPH)


@pytest.mark.asyncio
async def test_sorting_a_limited_result_orders_in_the_database(
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
        assert column_values(app, 0)[0] == 0

        # descending: the database orders all 600 rows, then the limit applies
        await click_header(app, pilot, wait_for_workers, 0)
        await click_header(app, pilot, wait_for_workers, 0)
        table = visible_table(app)
        assert table.row_count == 500
        assert column_values(app, 0)[:3] == [599, 598, 597]
        assert (
            editor_text(app) == 'select range as a from range(600)\norder by "a" desc'
        )


@pytest.mark.asyncio
async def test_other_statements_in_the_buffer_are_preserved(
    app: Harlequin, wait_for_workers: Waiter
) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        await ready(app, pilot, wait_for_workers)
        assert app.editor is not None
        app.editor.text = f"select 1 as x;\n\n{THREE_ROWS};"
        # run only the second statement
        app.editor.selection = app.editor.selection.__class__(
            (2, 0), (2, len(THREE_ROWS) + 1)
        )
        await pilot.press("ctrl+j")
        for _ in range(3):
            await wait_for_workers(app)
            await pilot.pause()
        assert visible_table(app).row_count == 3

        await click_header(app, pilot, wait_for_workers, 0)
        assert column_values(app, 0) == [1, 2, 3]
        assert editor_text(app) == f'select 1 as x;\n\n{THREE_ROWS}\norder by "a" asc;'


@pytest.mark.asyncio
async def test_computed_and_duplicated_columns_are_ordered_by_position(
    app: Harlequin, wait_for_workers: Waiter
) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        await ready(app, pilot, wait_for_workers)
        sql = "select a, a+1, a as a from (values (2), (1)) as t(a)"
        await run_sql(app, pilot, wait_for_workers, sql)

        # a+1 is displayed under a name that is not an identifier
        await click_header(app, pilot, wait_for_workers, 1)
        assert column_values(app, 1) == [2, 3]
        assert editor_text(app) == f"{sql}\norder by 2 asc"

        # the third column shares its name with the first
        await click_header(app, pilot, wait_for_workers, 2)
        assert editor_text(app) == f"{sql}\norder by 3 asc"
        assert column_values(app, 0) == [1, 2]
