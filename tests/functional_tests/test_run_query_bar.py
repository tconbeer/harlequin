from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from harlequin import Harlequin
from textual.message import Message


@pytest.mark.asyncio
async def test_run_query_bar(
    app_all_adapters_small_db: Harlequin, app_snapshot: Callable[..., Awaitable[bool]]
) -> None:
    app = app_all_adapters_small_db
    snap_results: list[bool] = []
    messages: list[Message] = []
    async with app.run_test(size=(120, 36), message_hook=messages.append) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        # initialization
        bar = app.run_query_bar
        assert bar.checkbox.value is False
        assert bar.input.value == "500"
        assert bar.limit_value is None

        # query without any limit by clicking the button;
        # dataset has 857 records
        app.editor.text = "select * from drivers"
        await pilot.click(bar.button.__class__)
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        table = app.results_viewer.get_visible_table()
        assert table
        assert table.row_count == table.source_row_count == 857
        snap_results.append(await app_snapshot(app, "No limit"))

        # apply a limit by clicking the limit checkbox
        await pilot.click(bar.checkbox.__class__)
        assert bar.checkbox.value is True
        assert bar.limit_value == 500
        await pilot.click(bar.button.__class__)
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        table = app.results_viewer.get_visible_table()
        assert table
        assert table.row_count == table.source_row_count == 500
        snap_results.append(await app_snapshot(app, "Limit 500"))

        # type an invalid limit, checkbox should be unchecked
        # and a tooltip should appear on hover
        await pilot.click(bar.input.__class__)
        await pilot.press("a")
        assert bar.input.value == "a500"
        assert bar.limit_value is None
        assert bar.checkbox.value is False
        assert bar.input.tooltip is not None
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "Invalid limit"))

        # type a valid limit
        await pilot.press("backspace")
        await pilot.press("delete")
        await pilot.press("1")
        assert bar.input.value == "100"
        assert bar.limit_value == 100
        assert bar.checkbox.value is True
        assert bar.input.tooltip is None

        # run the query with a smaller limit
        await pilot.click(bar.button.__class__)
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        table = app.results_viewer.get_visible_table()
        assert table
        assert table.row_count == table.source_row_count == 100
        snap_results.append(await app_snapshot(app, "Limit 100"))

        assert all(snap_results)
