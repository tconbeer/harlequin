from __future__ import annotations

import sys
from typing import Awaitable, Callable

import pytest
from textual.message import Message
from textual.pilot import Pilot

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.components.code_editor import CodeEditor
from harlequin.components.results_viewer import ResultsTable
from tests.waiting import wait_for, wait_for_value


@pytest.mark.asyncio
async def test_run_query_bar(
    app_all_adapters_small_db: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    transaction_button_visible: Callable[[Harlequin], bool],
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    app = app_all_adapters_small_db
    snap_results: list[bool] = []
    messages: list[Message] = []
    async with app.run_test(size=(120, 36), message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        # initialization
        bar = app.run_query_bar
        assert bar.limit_checkbox.value is False
        assert bar.limit_input.value == "500"
        assert bar.limit_value is None

        # query without any limit by clicking the button;
        # dataset has 857 records
        editor.text = "select * from drivers"
        await pilot.click("#run_query")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        table = app.results_viewer.get_visible_table()
        assert table
        assert table.row_count == table.source_row_count == 857
        assert table.fetch_truncated is False
        # nothing was cut short, so the count is exact and says so
        assert app.results_viewer.border_title == "Query Results (857 Records)"
        snap_results.append(await app_snapshot(app, "No limit"))

        # apply a limit by clicking the limit checkbox
        await pilot.click(bar.limit_checkbox.__class__)
        assert bar.limit_checkbox.value is True
        assert bar.limit_value == 500
        await pilot.click("#run_query")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        table = app.results_viewer.get_visible_table()
        assert table
        # 501 fetched: one row more than the limit is what proves there are
        # more, and it is not kept.
        assert table.row_count == 500
        assert table.source_row_count == 501
        assert table.fetch_truncated is True
        assert app.results_viewer.border_title == (
            "Query Results (Showing 500 of >500 Records)"
        )
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
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        table = app.results_viewer.get_visible_table()
        assert table
        assert table.row_count == 100
        assert table.source_row_count == 101
        snap_results.append(await app_snapshot(app, "Limit 100"))

        if not transaction_button_visible(app):
            assert all(snap_results)


@pytest.mark.py12
@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="SQLite in Python < 3.12 won't show txn button"
)
@pytest.mark.asyncio
async def test_transaction_button(
    app_small_sqlite: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    app = app_small_sqlite
    snap_results: list[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)
        connection = await wait_for_value(
            pilot, lambda: app.connection, description="the app to connect"
        )

        assert connection.transaction_mode
        assert connection.transaction_mode.label == "Auto"
        snap_results.append(await app_snapshot(app, "Initialize with Tx: Auto"))
        await pilot.click("#transaction_button")
        await wait_for(
            pilot,
            lambda: (
                connection.transaction_mode is not None
                and connection.transaction_mode.label == "Manual"
            ),
            description="the transaction mode to switch to Manual",
        )
        await pilot.wait_for_animation()
        assert connection.transaction_mode
        assert connection.transaction_mode.commit is not None
        assert connection.transaction_mode.rollback is not None
        snap_results.append(await app_snapshot(app, "After click with Tx: Manual"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_a_configured_limit_is_in_force_from_the_first_query(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
    wait_for_table: Callable[[Pilot, Harlequin], Awaitable[ResultsTable]],
) -> None:
    """`--limit` sets the input and checks the box, so it actually limits.

    Which is the whole of what makes it the same option as `hsql --limit`: the
    number in the bar is what `cursor.set_limit()` receives.
    """
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="limited",
        query_limit=10,
    )
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        bar = app.run_query_bar
        assert bar.limit_checkbox.value is True
        assert bar.limit_input.value == "10"
        assert bar.limit_value == 10

        editor.text = "select * from range(100)"
        await pilot.press("ctrl+j")
        table = await wait_for_table(pilot, app)
        assert table.row_count == 10
        assert table.fetch_truncated is True
        assert app.results_viewer.border_title == (
            "Query Results (Showing 10 of >10 Records)"
        )


@pytest.mark.asyncio
async def test_no_configured_limit_leaves_the_box_unchecked(
    app_small_duck: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    """A human watching a viewport can afford a full fetch, so nothing configured
    means nothing limited -- with 500 in the input to start from."""
    app = app_small_duck
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)
        assert app.run_query_bar.limit_checkbox.value is False
        assert app.run_query_bar.limit_input.value == "500"
        assert app.run_query_bar.limit_value is None
