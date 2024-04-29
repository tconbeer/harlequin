from __future__ import annotations

import sys
from typing import Awaitable, Callable

import pytest
from harlequin import Harlequin
from textual.message import Message


def transaction_button_visible(app: Harlequin) -> bool:
    """
    Skip snapshot checks for versions of that app showing the autocommit button.
    """
    return sys.version_info >= (3, 12) and "Sqlite" in app.adapter.__class__.__name__


@pytest.mark.asyncio
async def test_run_query_bar(
    app_all_adapters_small_db: Harlequin, app_snapshot: Callable[..., Awaitable[bool]]
) -> None:
    app = app_all_adapters_small_db
    snap_results: list[bool] = []
    messages: list[Message] = []
    async with app.run_test(size=(120, 36), message_hook=messages.append) as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None:
            await pilot.pause()
        # initialization
        bar = app.run_query_bar
        assert bar.limit_checkbox.value is False
        assert bar.limit_input.value == "500"
        assert bar.limit_value is None

        # query without any limit by clicking the button;
        # dataset has 857 records
        assert app.editor is not None
        app.editor.text = "select * from drivers"
        await pilot.click("#run_query")
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
        await pilot.click(bar.limit_checkbox.__class__)
        assert bar.limit_checkbox.value is True
        assert bar.limit_value == 500
        await pilot.click("#run_query")
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
        await pilot.click(bar.limit_input.__class__)
        await pilot.press("a")
        assert bar.limit_input.value == "a500"
        assert bar.limit_value is None
        assert bar.limit_checkbox.value is False
        assert bar.limit_input.tooltip is not None
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "Invalid limit"))

        # type a valid limit
        await pilot.press("backspace")
        await pilot.press("delete")
        await pilot.press("1")
        assert bar.limit_input.value == "100"
        assert bar.limit_value == 100
        assert bar.limit_checkbox.value is True
        assert bar.limit_input.tooltip is None

        # run the query with a smaller limit
        await pilot.click("#run_query")
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

        if not transaction_button_visible(app):
            assert all(snap_results)


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="SQLite in Python < 3.12 won't show txn button"
)
@pytest.mark.asyncio
async def test_transaction_button(
    app_small_sqlite: Harlequin, app_snapshot: Callable[..., Awaitable[bool]]
) -> None:
    app = app_small_sqlite
    snap_results: list[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None or app.connection is None:
            await pilot.pause()

        assert app.connection.transaction_mode
        assert app.connection.transaction_mode.label == "Auto"
        snap_results.append(await app_snapshot(app, "Initialize with Tx: Auto"))
        await pilot.click("#transaction_button")
        assert app.connection.transaction_mode
        assert app.connection.transaction_mode.label == "Manual"
        assert app.connection.transaction_mode.commit is not None
        assert app.connection.transaction_mode.rollback is not None
        snap_results.append(await app_snapshot(app, "After click with Tx: Manual"))

        assert all(snap_results)
