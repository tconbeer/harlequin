from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from rich.text import Text
from textual.pilot import Pilot
from textual_fastdatatable import DataTable

from harlequin import Harlequin
from harlequin.components.results_viewer import (
    SORT_ASC_GLYPH,
    SORT_DESC_GLYPH,
    SORT_HINT_GLYPH,
    ResultsTable,
)

Waiter = Callable[[Harlequin], Awaitable[None]]


def visible_table(app: Harlequin) -> ResultsTable:
    table = app.results_viewer.get_visible_table()
    assert table is not None
    return table


def header_glyphs(app: Harlequin) -> list[str]:
    return [str(column.label) for column in visible_table(app).ordered_columns]


async def run_sql(app: Harlequin, pilot: Pilot, wait: Waiter, sql: str) -> ResultsTable:
    """Put `sql` in the editor, run it, and return the table it produced."""
    assert app.editor is not None
    app.editor.text = sql
    await pilot.press("ctrl+j")
    await wait(app)
    await pilot.pause()
    await wait(app)
    await pilot.pause()
    return visible_table(app)


async def click_header(app: Harlequin, pilot: Pilot, wait: Waiter, index: int) -> None:
    table = visible_table(app)
    table.post_message(DataTable.HeaderSelected(table, index, label=Text()))
    await pilot.pause()
    await wait(app)
    await pilot.pause()


async def ready(app: Harlequin, pilot: Pilot, wait: Waiter) -> None:
    await wait(app)
    while app.editor is None:
        await pilot.pause()


@pytest.mark.asyncio
async def test_click_cycles_ascending_then_descending(
    app: Harlequin, wait_for_workers: Waiter
) -> None:
    async with app.run_test() as pilot:
        await ready(app, pilot, wait_for_workers)
        table = await run_sql(app, pilot, wait_for_workers, "select 1 as a, 2 as b")
        assert table.sort_column is None
        assert app.editor is not None

        await click_header(app, pilot, wait_for_workers, 0)
        assert 'order by "a" asc' in app.editor.text
        assert (visible_table(app).sort_column, visible_table(app).sort_descending) == (
            "a",
            False,
        )

        # clicking the same header again flips the direction
        await click_header(app, pilot, wait_for_workers, 0)
        assert 'order by "a" desc' in app.editor.text
        assert (visible_table(app).sort_column, visible_table(app).sort_descending) == (
            "a",
            True,
        )

        # and a third click loops back, without stacking clauses
        await click_header(app, pilot, wait_for_workers, 0)
        assert 'order by "a" asc' in app.editor.text
        assert app.editor.text.count("order by") == 1


@pytest.mark.asyncio
async def test_clicking_a_different_column_starts_ascending(
    app: Harlequin, wait_for_workers: Waiter
) -> None:
    async with app.run_test() as pilot:
        await ready(app, pilot, wait_for_workers)
        table = await run_sql(
            app, pilot, wait_for_workers, 'select 1 as a, 2 as b order by "a" desc'
        )
        assert (table.sort_column, table.sort_descending) == ("a", True)
        assert app.editor is not None

        await click_header(app, pilot, wait_for_workers, 1)  # column b
        assert 'order by "b" asc' in app.editor.text
        assert app.editor.text.count("order by") == 1


@pytest.mark.asyncio
async def test_headers_show_hint_and_direction_glyphs(
    app: Harlequin, wait_for_workers: Waiter
) -> None:
    async with app.run_test() as pilot:
        await ready(app, pilot, wait_for_workers)
        await run_sql(app, pilot, wait_for_workers, "select 1 as a, 2 as b")

        labels = header_glyphs(app)
        assert all(SORT_HINT_GLYPH in label for label in labels)
        assert not any(SORT_ASC_GLYPH in label for label in labels)

        await click_header(app, pilot, wait_for_workers, 0)
        labels = header_glyphs(app)
        assert SORT_ASC_GLYPH in labels[0]
        assert SORT_HINT_GLYPH in labels[1]  # the unsorted column keeps its hint

        await click_header(app, pilot, wait_for_workers, 0)
        assert SORT_DESC_GLYPH in header_glyphs(app)[0]


@pytest.mark.asyncio
async def test_a_hand_written_order_by_lights_up_its_header(
    app: Harlequin, wait_for_workers: Waiter
) -> None:
    """The indicator is read off the query, so typing ORDER BY marks the header."""
    async with app.run_test() as pilot:
        await ready(app, pilot, wait_for_workers)
        table = await run_sql(
            app, pilot, wait_for_workers, 'select 1 as a, 2 as b order by "b" desc'
        )
        assert (table.sort_column, table.sort_descending) == ("b", True)

        labels = header_glyphs(app)
        assert SORT_HINT_GLYPH in labels[0]
        assert SORT_DESC_GLYPH in labels[1]


@pytest.mark.asyncio
async def test_other_queries_in_the_buffer_are_preserved(
    app: Harlequin, wait_for_workers: Waiter
) -> None:
    """Sorting rewrites only the query that produced the visible result."""
    async with app.run_test() as pilot:
        await ready(app, pilot, wait_for_workers)
        await run_sql(app, pilot, wait_for_workers, "select 1 as a")
        assert app.editor is not None

        app.editor.text = "select 9 as keep_me;\nselect 1 as a"
        await pilot.pause()

        await click_header(app, pilot, wait_for_workers, 0)
        assert "select 9 as keep_me;" in app.editor.text
        assert 'order by "a" asc' in app.editor.text
