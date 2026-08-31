from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from rich.align import Align
from rich.cells import cell_len
from rich.text import Text
from textual.coordinate import Coordinate
from textual.pilot import Pilot

from harlequin import Harlequin
from harlequin.components.results_viewer import FOREIGN_KEY_GLYPH

FOREIGN_KEYS = {1: ('"main"."other"', '"id"'), 2: ('"main"."other"', '"name"')}


@pytest.fixture
def duckdb_reports_foreign_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """DuckDB has no foreign-key metadata; pretend two result columns are keys."""
    monkeypatch.setattr(
        "harlequin_duckdb.adapter.DuckDbCursor.foreign_key_columns",
        lambda self: FOREIGN_KEYS,
    )


async def run_query(
    app: Harlequin,
    pilot: Pilot[None],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    query: str,
) -> None:
    assert app.editor is not None
    app.editor.text = query
    await pilot.press("ctrl+j")
    await wait_for_workers(app)
    await pilot.pause()
    await wait_for_workers(app)
    await pilot.pause()


@pytest.mark.asyncio
async def test_foreign_key_navigation(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    duckdb_reports_foreign_keys: None,
) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        await run_query(
            app,
            pilot,
            wait_for_workers,
            "create table other as select 2 as id, 'x' as name",
        )
        await run_query(
            app,
            pilot,
            wait_for_workers,
            "select 1 as id, 2 as other_id, 'x' as other_name "
            "union all select 3, null, null",
        )
        table = app.results_viewer.get_visible_table()
        assert table is not None
        assert table.foreign_keys == FOREIGN_KEYS

        # a right-aligned number carries the glyph after it, text in front of it
        number = table._get_cell_renderable(0, 1)
        assert isinstance(number, Align)
        assert isinstance(number.renderable, Text)
        assert number.renderable.plain == f"2 {FOREIGN_KEY_GLYPH}"
        text = table._get_cell_renderable(0, 2)
        assert isinstance(text, Text)
        assert text.plain == f"{FOREIGN_KEY_GLYPH} x"
        # nulls and non-key columns are left alone, and the raw value is untouched
        null_cell = table._get_cell_renderable(1, 1)
        assert isinstance(null_cell, Align)
        assert FOREIGN_KEY_GLYPH not in str(null_cell.renderable)
        plain_cell = table._get_cell_renderable(0, 0)
        assert isinstance(plain_cell, Align)
        assert FOREIGN_KEY_GLYPH not in str(plain_cell.renderable)
        assert table.get_cell_at(Coordinate(0, 1)) == 2
        # the key column grew to fit the glyph
        label_width = cell_len(table.ordered_columns[1].label.plain)
        marker_width = cell_len(f"{FOREIGN_KEY_GLYPH} ")
        assert table.ordered_columns[1].content_width == label_width + marker_width

        # clicking the glyph opens the referenced row in a new buffer and runs it
        cell = table._get_cell_region(Coordinate(0, 1))
        await pilot.click(table, offset=(cell.x + cell.width - 2, cell.y))
        await pilot.pause()
        assert app.editor_collection.tab_count == 2
        assert app.editor.text == 'select *\nfrom "main"."other"\nwhere "id" = 2'
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        followed = app.results_viewer.get_visible_table()
        assert followed is not None
        assert followed.row_count == 1
        assert followed.get_cell_at(Coordinate(0, 0)) == 2
