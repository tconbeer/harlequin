from typing import Awaitable, Callable

import pytest
from harlequin import Harlequin


@pytest.mark.asyncio
async def test_dupe_column_names(
    app_all_adapters: Harlequin, app_snapshot: Callable[..., Awaitable[bool]]
) -> None:
    app = app_all_adapters
    query = "select 1 as a, 1 as a, 2 as a, 2 as a"
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await pilot.pause()

        assert app.query_text == query
        assert app.cursors
        assert await app_snapshot(app, "dupe columns")
