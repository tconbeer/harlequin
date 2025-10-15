import asyncio
from unittest.mock import patch

from textual.widgets.text_area import Selection

from harlequin import Harlequin
from harlequin.editor_cache import BufferState, Cache
from harlequin_duckdb import DuckDbAdapter


async def wait_for_filtered_workers(app: Harlequin) -> None:
    filtered_workers = [
        w for w in app.workers if w.name != "_database_tree_background_loader"
    ]
    if filtered_workers:
        await app.workers.wait_for_complete(filtered_workers)


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
            await wait_for_filtered_workers(app)
            await pilot.pause()
        app.exit()


def main() -> None:
    asyncio.run(load_lots_of_buffers())
    print("Ran successfully")


if __name__ == "__main__":
    main()
