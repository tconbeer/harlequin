import asyncio
from unittest.mock import patch

from harlequin import Harlequin
from harlequin.editor_cache import BufferState, Cache
from harlequin_duckdb import DuckDbAdapter
from textual.widgets.text_area import Selection


async def load_lots_of_buffers() -> None:
    with patch("harlequin.components.code_editor.load_cache") as mock_load_cache:
        buff = BufferState(
            selection=Selection((0, 0), (0, 0)),
            text="select 1000; ",
        )
        cache = Cache(focus_index=0, buffers=[buff])
        mock_load_cache.return_value = cache

        adapter = DuckDbAdapter((":memory:",), no_init=True)
        app = Harlequin(adapter=adapter)

        async with app.run_test() as pilot:
            while app.editor is None:
                await pilot.pause()
            await pilot.press("ctrl+j")
            while (table := app.results_viewer.get_visible_table()) is None:
                await pilot.pause()
            assert table.row_count == 1

        app.exit()


asyncio.run(load_lots_of_buffers())
print("Ran successfully")
