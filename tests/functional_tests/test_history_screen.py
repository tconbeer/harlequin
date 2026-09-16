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
from harlequin.components.code_editor import CodeEditor
from harlequin.history import History
from harlequin.query import fetch
from harlequin.query_log import QueryLog
from tests.waiting import wait_for_value


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
    return await wait_for_value(
        pilot,
        lambda: app.screen if isinstance(app.screen, HistoryScreen) else None,
        description="the History screen",
    )


@pytest.mark.asyncio
async def test_history_screen(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_time: None,
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    snap_results: list[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        # rich.Syntax calculates different colors for the line numbers, depending
        # on the color system of the rich.console, which is different across different
        # GitHub action runners. Here we force everything to truecolor.
        app.console._color_system = COLOR_SYSTEMS["truecolor"]
        q = [f"select {i};" for i in range(15)]
        await wait_for_editor(pilot, app)
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

        await pilot.press("enter")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "New buffer with select 14"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_a_second_request_does_not_stack_history_screens(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_time: None,
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    """https://github.com/tconbeer/harlequin/issues/485"""
    async with app.run_test() as pilot:
        q = [f"select {i};" for i in range(15)]
        await wait_for_editor(pilot, app)
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
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    """As each statement runs, not at quit."""
    async with app.run_test() as pilot:
        await wait_for_editor(pilot, app)
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
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
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
        await wait_for_editor(pilot, app)
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
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
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
        await wait_for_editor(pilot, app)
        connection = await wait_for_value(
            pilot, lambda: app.connection, description="the app to connect"
        )
        app.query_log.update = watched_update  # type: ignore[method-assign]
        with (
            patch.object(harlequin.app, "fetch", slow_fetch),
            patch.object(app.adapter, "IMPLEMENTS_CANCEL", True),
            patch.object(connection, "cancel", fake_cancel),
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
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    """Only what the fetch never completed is `canceled`."""
    async with app.run_test() as pilot:
        await wait_for_editor(pilot, app)
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
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    """`--no-write-history`, or the key of that name in the profile."""
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        record_history=False,
    )
    async with app.run_test() as pilot:
        await wait_for_editor(pilot, app)
        app.post_message(QuerySubmitted(queries=["select 1;"], limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        assert not query_log_path.exists()


@pytest.mark.asyncio
async def test_the_screen_shows_what_another_command_ran(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    """One store, so an agent's queries reach the human's History screen."""
    agent = QueryLog(program="hsql", connection="foo")
    agent.write("select * from line_items", rows=3, elapsed_ms=12.5)
    agent.close()

    async with app.run_test() as pilot:
        await wait_for_editor(pilot, app)
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
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    elsewhere = QueryLog(program="hsql", connection="another-database")
    elsewhere.write("select * from orders")
    elsewhere.close()

    async with app.run_test() as pilot:
        await wait_for_editor(pilot, app)
        screen = await open_history(pilot, app, wait_for_workers)
        assert len(screen.history) == 0


@pytest.mark.asyncio
async def test_the_screen_opens_before_anything_has_been_run(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    """An empty store is an empty screen rather than an error."""
    async with app.run_test() as pilot:
        await wait_for_editor(pilot, app)
        screen = await open_history(pilot, app, wait_for_workers)
        assert len(screen.history) == 0


@pytest.mark.asyncio
async def test_a_pickled_history_arrives_in_the_screen(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    legacy_history_cache: Path,
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    """The one-time move: what an older Harlequin saved is still there."""
    async with app.run_test() as pilot:
        await wait_for_editor(pilot, app)
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
    wait_for_editor: Callable[[Pilot, Harlequin], Awaitable[CodeEditor]],
) -> None:
    """`--no-write-history` is a refusal to write, and the move is a write."""
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        record_history=False,
    )
    async with app.run_test() as pilot:
        await wait_for_editor(pilot, app)
        screen = await open_history(pilot, app, wait_for_workers)
        assert len(screen.history) == 0
        assert not query_log_path.exists()
