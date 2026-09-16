from __future__ import annotations

import threading
from typing import Awaitable, Callable

import pytest
from textual.message import Message

from harlequin import Harlequin
from harlequin.app import QuerySubmitted, ResultsFetched, TzDataDownloadStarted
from harlequin.exception import HarlequinTzDataError


@pytest.mark.asyncio
async def test_the_app_starts_while_the_tzdata_download_is_in_flight(
    app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The editor is up and the catalog is loaded before the download returns,
    but a fetch waits for it, since Arrow needs the database to build a
    timestamptz column."""
    download_started = threading.Event()
    finish_download = threading.Event()

    def blocking_download() -> None:
        download_started.set()
        assert finish_download.wait(timeout=10.0), "the fetch never released it"

    monkeypatch.setattr("harlequin.app.find_tzdata", lambda: False)
    monkeypatch.setattr("harlequin.app.download_tzdata", blocking_download)
    app.wants_tzdata = True
    app._tzdata_ready.clear()

    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        while app.editor is None:
            await pilot.pause()
        assert download_started.wait(timeout=10.0)
        assert [m for m in messages if isinstance(m, TzDataDownloadStarted)]

        app.post_message(QuerySubmitted(queries=["select 1 as foo"], limit=None))
        for _ in range(20):
            await pilot.pause()
        assert not [m for m in messages if isinstance(m, ResultsFetched)]

        finish_download.set()
        await wait_for_workers(app)
        await pilot.pause()
        [results_fetched] = [m for m in messages if isinstance(m, ResultsFetched)]
        assert results_fetched.errors == []


@pytest.mark.asyncio
async def test_a_failed_tzdata_download_is_a_warning(
    app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A session with no timezone database is usable, so it is not an error
    modal and it does not stop a query from running."""

    def failed_download() -> None:
        raise HarlequinTzDataError(msg="No network.", title="Harlequin Timezone Error")

    monkeypatch.setattr("harlequin.app.find_tzdata", lambda: False)
    monkeypatch.setattr("harlequin.app.download_tzdata", failed_download)
    app.wants_tzdata = True
    app._tzdata_ready.clear()

    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app._tzdata_ready.is_set()
        failures = [
            n
            for n in app._notifications
            if n.title == "Harlequin Timezone Error" and n.severity == "warning"
        ]
        assert [n for n in failures if "No network." in n.message]

        app.post_message(QuerySubmitted(queries=["select 1 as foo"], limit=None))
        await wait_for_workers(app)
        await pilot.pause()
        [results_fetched] = [m for m in messages if isinstance(m, ResultsFetched)]
        assert results_fetched.errors == []
