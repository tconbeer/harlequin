import asyncio
from unittest.mock import patch

from textual.widgets.text_area import Selection

from harlequin import Harlequin
from harlequin.editor_cache import BufferState, Cache
from harlequin_duckdb import DuckDbAdapter


async def load_lots_of_buffers() -> None:
    with patch("harlequin.components.code_editor.load_cache") as mock_load_cache:
        buff = BufferState(
            selection=Selection((0, 0), (0, 0)),
            text="select 1; " * 20,
        )
        cache = Cache(focus_index=0, buffers=[buff] * 10)
        mock_load_cache.return_value = cache

        adapter = DuckDbAdapter((":memory:",), no_init=True)
        app = Harlequin(adapter=adapter)

        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
        app.exit()


asyncio.run(load_lots_of_buffers())
print("Ran successfully")
