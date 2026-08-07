from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from rich.text import Text

from harlequin import Harlequin


@pytest.mark.asyncio
async def test_foreign_key_cell_shows_glyph(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A result column marked as a foreign key renders its value with a trailing
    glyph, while the raw value (get_cell_at) is left untouched."""
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select 1 as id, 2 as other_id"
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        table = app.results_viewer.get_visible_table()
        assert table is not None
        # mark column 1 (other_id) as a foreign key
        table.fk_map = {1: ('"main"."other"', '"id"')}

        fk_cell = table._get_cell_renderable(0, 1)
        plain_cell = table._get_cell_renderable(0, 0)

        from textual.coordinate import Coordinate

        assert isinstance(fk_cell, Text)
        assert "↗" in fk_cell.plain  # fk column gets the glyph
        plain_text = getattr(plain_cell, "plain", str(plain_cell))
        assert "↗" not in plain_text  # a non-fk column does not
        # the raw value is preserved for navigation/editing
        assert table.get_cell_at(Coordinate(0, 1)) == 2
