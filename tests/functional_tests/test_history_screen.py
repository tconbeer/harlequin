from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest.mock import MagicMock, patch

import pytest
from rich.console import COLOR_SYSTEMS
from textual.pilot import Pilot

import harlequin.app
from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.app import QueryHistoryLoaded, QuerySubmitted
from harlequin.components import HistoryScreen
from harlequin.components.help_screen import HelpScreen
from harlequin.components.history_screen import FILTER_INTERVAL
from harlequin.history import DEFAULT_ROWS, History
from harlequin.query import fetch
from harlequin.query_log import QueryLog


@pytest.fixture
def mock_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix what the store records, so the screen that reads it back snapshots.

    The timestamps are built from a local wall-clock time and written in UTC,
    which is how a record made anywhere renders as the same row: the screen
    shows the reader's own time zone.
    """
    base = datetime(2024, 1, 26, hour=10).astimezone()
    mock_datetime = MagicMock()
    mock_datetime.now.side_effect = (base + timedelta(minutes=i) for i in range(1000))
    monkeypatch.setattr("harlequin.query_log.datetime", mock_datetime)

    for module in ("harlequin.app", "harlequin.query"):
        mock_time = MagicMock()
        mock_time.monotonic.side_effect = (float(i) for i in range(1000))
        monkeypatch.setattr(f"{module}.time", mock_time)


async def open_history(
    pilot: Pilot,
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> HistoryScreen:
    """Press the key that opens the History screen, and return the screen."""
    await pilot.press("f8")
    await wait_for_workers(app)
    await pilot.pause()
    assert isinstance(app.screen, HistoryScreen)
    return app.screen


async def type_filter(
    pilot: Pilot,
    app: Harlequin,
    term: str,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Type into the filter and wait for the read it debounces to land."""
    await pilot.press(*term)
    await pilot.pause(FILTER_INTERVAL * 2)
    await wait_for_workers(app)
    await pilot.pause()


@pytest.mark.asyncio
async def test_history_screen(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_time: None,
) -> None:
    snap_results: list[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        # rich.Syntax calculates different colors for the line numbers, depending
        # on the color system of the rich.console, which is different across different
        # GitHub action runners. Here we force everything to truecolor.
        app.console._color_system = COLOR_SYSTEMS["truecolor"]
        q = [f"select {i};" for i in range(15)]
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(queries=q, limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        # run a bad query
        app.post_message(QuerySubmitted(queries=["sel;"], limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.press("space")

        await open_history(pilot, app, wait_for_workers)
        await pilot.press("down")
        snap_results.append(await app_snapshot(app, "History Viewer"))

        await type_filter(pilot, app, "select 1", wait_for_workers)
        snap_results.append(await app_snapshot(app, "Filtered History Viewer"))

        await pilot.press("escape")
        await pilot.pause(FILTER_INTERVAL * 2)
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "New buffer with select 14"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_a_second_request_does_not_stack_history_screens(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_time: None,
) -> None:
    """https://github.com/tconbeer/harlequin/issues/485"""
    async with app.run_test() as pilot:
        q = [f"select {i};" for i in range(15)]
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(queries=q, limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        screen = await open_history(pilot, app, wait_for_workers)
        await pilot.press("f8")
        await wait_for_workers(app)
        await pilot.pause()

        # and a read that lands after the screen is already up is dropped too,
        # which is the half a keypress cannot reach
        app.post_message(QueryHistoryLoaded(history=History(queries=[])))
        await pilot.pause()

        assert app.is_running
        assert app.screen is screen
        assert [s for s in app.screen_stack if isinstance(s, HistoryScreen)] == [screen]


def logged(store: Path) -> list[dict[str, Any]]:
    """Every row the IDE wrote to the query log, oldest first."""
    db = sqlite3.connect(store)
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute("select * from queries order by id")]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_a_query_is_recorded_as_it_runs(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    query_log_path: Path,
) -> None:
    """As each statement runs, not at quit."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(queries=["select 1 as a;", "sel;"], limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        recorded = {row["sql"]: row for row in logged(query_log_path)}
        assert set(recorded) == {"select 1 as a;", "sel;"}
        assert all(row["program"] == "harlequin" for row in recorded.values())
        assert all(row["connection"] == "foo" for row in recorded.values())
        assert recorded["select 1 as a;"]["status"] == "ok"
        assert recorded["select 1 as a;"]["rows"] == 1
        assert recorded["sel;"]["status"] == "error"
        assert recorded["sel;"]["error"]


@pytest.mark.asyncio
async def test_a_row_exists_before_its_rows_are_known(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    query_log_path: Path,
) -> None:
    """The two-phase write: a session that dies mid-fetch keeps the query.

    Asserted by holding the fetch open, which is the window a crash lands in.
    """
    fetching = threading.Event()
    release = threading.Event()
    real_fetch = fetch

    def slow_fetch(*args: Any, **kwargs: Any) -> Any:
        fetching.set()
        release.wait(timeout=10)
        return real_fetch(*args, **kwargs)

    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        with patch.object(harlequin.app, "fetch", slow_fetch):
            app.post_message(QuerySubmitted(queries=["select 1 as a;"], limit=None))
            assert await asyncio.get_running_loop().run_in_executor(
                None, fetching.wait, 10
            ), "the fetch never started"

            # mid-fetch: the row is already there, with its rows not yet known
            (pending,) = logged(query_log_path)
            assert pending["sql"] == "select 1 as a;"
            assert pending["status"] == "ok"
            assert pending["rows"] is None

            release.set()
            await wait_for_workers(app)
            await pilot.pause()

        # and the same row is completed rather than a second one written
        (final,) = logged(query_log_path)
        assert final["id"] == pending["id"]
        assert final["rows"] == 1
        assert final["elapsed_ms"] is not None


@pytest.mark.asyncio
async def test_a_query_cancelled_mid_fetch_is_recorded_as_canceled(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    query_log_path: Path,
) -> None:
    """The cancel has to mark the rows before it cancels the cursor.

    A cancelled cursor comes back empty and error-free, so a fetch that
    completes in the window between `cancel()` returning and the marking would
    record `ok` with no rows -- indistinguishable from a query that matched
    nothing, which is the ambiguity `canceled` exists to remove. The fake
    `cancel()` here puts the fetch's completion inside that window on purpose,
    rather than racing for it.
    """
    fetching = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def slow_fetch(*args: Any, **kwargs: Any) -> Any:
        fetching.set()
        release.wait(timeout=10)
        return fetch(*args, **kwargs)

    real_update = app.query_log.update

    def watched_update(row: int | None, **kwargs: Any) -> None:
        real_update(row, **kwargs)
        completed.set()

    def fake_cancel() -> None:
        # let the fetch run to completion, inside the cancel
        release.set()
        completed.wait(timeout=10)

    async with app.run_test() as pilot:
        while app.editor is None or app.connection is None:
            await pilot.pause()
        app.query_log.update = watched_update  # type: ignore[method-assign]
        with (
            patch.object(harlequin.app, "fetch", slow_fetch),
            patch.object(app.adapter, "IMPLEMENTS_CANCEL", True),
            patch.object(app.connection, "cancel", fake_cancel),
        ):
            app.post_message(QuerySubmitted(queries=["select 1 as a;"], limit=None))
            assert await asyncio.get_running_loop().run_in_executor(
                None, fetching.wait, 10
            ), "the fetch never started"

            app._cancel_query()
            await wait_for_workers(app)
            await pilot.pause()

        (record,) = logged(query_log_path)
        assert record["status"] == "canceled", (
            "the fetch completed the row before the cancel marked it"
        )


@pytest.mark.asyncio
async def test_a_cancel_leaves_a_finished_query_alone(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    query_log_path: Path,
) -> None:
    """Only what the fetch never completed is `canceled`."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(queries=["select 1 as a;"], limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        assert logged(query_log_path)[0]["status"] == "ok"

        app._cancel_query()
        await wait_for_workers(app)
        await pilot.pause()

        (record,) = logged(query_log_path)
        assert record["status"] == "ok", "a cancel rewrote a query that had finished"
        assert record["rows"] == 1


@pytest.mark.asyncio
async def test_a_session_that_records_nothing_still_runs_queries(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    query_log_path: Path,
) -> None:
    """`--no-write-history`, or the key of that name in the profile."""
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        record_history=False,
    )
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(queries=["select 1;"], limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        assert not query_log_path.exists()


@pytest.mark.asyncio
async def test_the_screen_shows_what_another_command_ran(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """One store, so an agent's queries reach the human's History screen."""
    agent = QueryLog(program="hsql", connection="foo")
    agent.write("select * from line_items", rows=3, elapsed_ms=12.5)
    agent.close()

    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(queries=["select 1;"], limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        screen = await open_history(pilot, app, wait_for_workers)
        assert [record.query_text for record in screen.history] == [
            "select 1;",
            "select * from line_items",
        ]


@pytest.mark.asyncio
async def test_the_screen_shows_only_this_connections_queries(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    elsewhere = QueryLog(program="hsql", connection="another-database")
    elsewhere.write("select * from orders")
    elsewhere.close()

    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)
        assert len(screen.history) == 0


@pytest.mark.asyncio
async def test_the_screen_opens_before_anything_has_been_run(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """An empty store is an empty screen rather than an error."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)
        assert len(screen.history) == 0


@pytest.mark.asyncio
async def test_a_pickled_history_arrives_in_the_screen(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    legacy_history_cache: Path,
) -> None:
    """The one-time move: what an older Harlequin saved is still there."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)
        assert [record.query_text for record in screen.history] == [
            "select * from line_items"
        ]


@pytest.mark.asyncio
async def test_a_session_that_records_nothing_moves_no_pickle(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    legacy_history_cache: Path,
    query_log_path: Path,
) -> None:
    """`--no-write-history` is a refusal to write, and the move is a write."""
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        record_history=False,
    )
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)
        assert len(screen.history) == 0
        assert not query_log_path.exists()


@pytest.mark.asyncio
async def test_an_app_binding_does_not_reach_through_the_screen(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """https://github.com/tconbeer/harlequin/issues/850"""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)
        assert not app.full_screen

        for key in ("f1", "f6", "f10", "f12", "ctrl+r"):
            await pilot.press(key)
            await pilot.pause()
            assert app.screen is screen, f"{key} pushed a screen"
        assert not [s for s in app.screen_stack if isinstance(s, HelpScreen)]
        assert not app.full_screen, "f10 reached the app"


@pytest.mark.asyncio
async def test_no_cursor_lands_in_the_preview(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The preview is for reading, so neither tab nor a click focuses it."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(queries=["select 1;"], limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)
        unfocusable = (screen.preview, screen.preview.text_input)

        for _ in range(4):
            await pilot.press("tab")
            await pilot.pause()
            assert screen.focused not in unfocusable

        await pilot.click(screen.preview)
        await pilot.pause()
        assert screen.focused not in unfocusable


@pytest.mark.asyncio
async def test_the_filter_searches_the_whole_store(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """https://github.com/tconbeer/harlequin/issues/429

    The filter is a query over the store, so it reaches queries older than the
    ones the screen holds.
    """
    log = QueryLog(program="hsql", connection="foo")
    log.write("select * from line_items")
    for i in range(DEFAULT_ROWS):
        log.write(f"select {i} from orders")
    log.close()

    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)
        listed = [record.query_text for record in screen.history]
        assert len(listed) == DEFAULT_ROWS
        assert "select * from line_items" not in listed

        await type_filter(pilot, app, "line_items", wait_for_workers)
        assert [record.query_text for record in screen.history] == [
            "select * from line_items"
        ]


@pytest.mark.asyncio
async def test_the_filter_matches_no_wildcards(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A typed `_` matches an underscore, not `like`'s any-character."""
    log = QueryLog(program="hsql", connection="foo")
    log.write("select * from lineXitems")
    log.close()

    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)
        await type_filter(pilot, app, "line_items", wait_for_workers)
        assert len(screen.history) == 0


@pytest.mark.asyncio
async def test_escape_clears_the_filter_before_it_closes_the_screen(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(queries=["select 1;"], limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)

        await type_filter(pilot, app, "nothing matches this", wait_for_workers)
        assert len(screen.history) == 0

        await pilot.press("escape")
        await pilot.pause(FILTER_INTERVAL * 2)
        await wait_for_workers(app)
        await pilot.pause()
        assert app.screen is screen
        assert screen.filter_input.value == ""
        assert [record.query_text for record in screen.history] == ["select 1;"]

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen


@pytest.mark.asyncio
async def test_enter_selects_the_highlighted_query_from_the_filter(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The filter holds focus, so enter has to reach the list past its submit."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        app.post_message(
            QuerySubmitted(queries=["select 1;", "select 2 as orders;"], limit=None)
        )
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)

        await type_filter(pilot, app, "orders", wait_for_workers)
        assert screen.filter_input.has_focus
        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is not screen
        assert app.editor is not None
        assert app.editor.text == "select 2 as orders;"
