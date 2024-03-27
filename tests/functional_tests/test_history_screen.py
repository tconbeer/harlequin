from __future__ import annotations

from datetime import datetime, timedelta
from typing import Awaitable, Callable
from unittest.mock import MagicMock

import pytest
from harlequin import Harlequin
from harlequin.app import QuerySubmitted
from rich.console import COLOR_SYSTEMS


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
    mock_time: None,
) -> None:
    snap_results: list[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        # rich.Syntax calculates different colors for the line numbers, depending
        # on the color system of the rich.console, which is different across different
        # GitHub action runners. Here we force everything to truecolor.
        app.console._color_system = COLOR_SYSTEMS["truecolor"]
        q = "\n".join([f"select {i};" for i in range(15)])
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(query_text=q, limit=None))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        # run a bad query
        app.post_message(QuerySubmitted(query_text="sel;", limit=None))
        await pilot.pause()
        await app.workers.wait_for_complete()
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
    mock_time: None,
) -> None:
    async with app.run_test() as pilot:
        q = "\n".join([f"select {i};" for i in range(15)])
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(query_text=q, limit=None))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        # https://github.com/tconbeer/harlequin/issues/485
        await pilot.press("f8")
        await pilot.press("f8")
