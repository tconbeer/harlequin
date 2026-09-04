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

import harlequin.app
from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.app import QuerySubmitted
from harlequin.query import fetch


@pytest.fixture
def mock_time(monkeypatch: pytest.MonkeyPatch) -> None:
    base = datetime(2024, 1, 26, hour=10)
    mock_datetime = MagicMock()
    mock_datetime.now.side_effect = (base + timedelta(minutes=i) for i in range(1000))
    monkeypatch.setattr("harlequin.history.datetime", mock_datetime)

    mock_time = MagicMock()
    mock_time.monotonic.side_effect = (float(i) for i in range(1000))
    monkeypatch.setattr("harlequin.app.time", mock_time)


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

        await pilot.press("f8")
        await pilot.press("down")
        snap_results.append(await app_snapshot(app, "History Viewer"))

        await pilot.press("enter")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "New buffer with select 14"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_history_screen_crash(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_time: None,
) -> None:
    async with app.run_test() as pilot:
        q = [f"select {i};" for i in range(15)]
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(queries=q, limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        # https://github.com/tconbeer/harlequin/issues/485
        await pilot.press("f8")
        await pilot.press("f8")


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

        assert app.history is not None, "the in-memory history is not the store"
        assert not query_log_path.exists()
