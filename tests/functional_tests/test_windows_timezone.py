from __future__ import annotations

import sys
import threading
from typing import Awaitable, Callable

import pytest
from textual.message import Message
from textual.pilot import Pilot

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.app import QuerySubmitted, ResultsFetched, TzDataDownloadStarted
from harlequin.exception import HarlequinTzDataError
from tests.functional_tests.helpers import wait_for_any_table, wait_for_editor
from tests.waiting import settle_app, wait_for_messages


async def _await_results(
    app: Harlequin, pilot: Pilot, messages: list[Message]
) -> ResultsFetched:
    """The fetch's message, once its tab has mounted.

    `wait_for_workers()` cannot stand in for this: a `QuerySubmitted` that has
    not been handled yet has started no worker, so it returns with nothing to
    wait for. And `TabbedContent.add_pane` activates the tab it just added,
    which `Tabs` refuses until that tab is in its list -- so a test that stops
    pumping in between can tear down mid-mount.
    """
    await wait_for_messages(pilot, messages, ResultsFetched)
    await wait_for_any_table(pilot, app)
    return [m for m in messages if isinstance(m, ResultsFetched)][-1]


@pytest.fixture
def windows_app(
    monkeypatch: pytest.MonkeyPatch, duckdb_adapter: type[HarlequinAdapter]
) -> Harlequin:
    """An app that believes it is on Windows, which is the only platform that
    looks for a timezone database.

    Built inside the fixture after the patch, because `__init__` is what reads
    `sys.platform` -- the app fixture is constructed before a test body runs.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    return Harlequin(duckdb_adapter([":memory:"], no_init=True), connection_hash="foo")


@pytest.mark.asyncio
async def test_the_app_starts_while_the_tzdata_download_is_in_flight(
    windows_app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The editor is up before the download returns, but a fetch waits for it,
    since Arrow needs the database to build a timestamptz column."""
    app = windows_app
    download_started = threading.Event()
    finish_download = threading.Event()

    def blocking_download(stop: threading.Event | None = None) -> None:
        download_started.set()
        assert finish_download.wait(timeout=10.0), "the fetch never released it"

    monkeypatch.setattr("harlequin.app.locate_tzdata", lambda: False)
    monkeypatch.setattr("harlequin.app.download_tzdata", blocking_download)

    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_editor(pilot, app)
        assert download_started.wait(timeout=10.0)
        assert [m for m in messages if isinstance(m, TzDataDownloadStarted)]

        app.post_message(QuerySubmitted(queries=["select 1 as foo"], limit=None))
        await settle_app(pilot)
        assert not [m for m in messages if isinstance(m, ResultsFetched)]

        finish_download.set()
        assert (await _await_results(app, pilot, messages)).errors == []


@pytest.mark.asyncio
async def test_a_fetch_gives_up_on_a_download_that_never_finishes(
    windows_app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wait is bounded: a stalled download delays a query, it does not
    strand the thread or the user."""
    app = windows_app
    release_download = threading.Event()

    def stalled_download(stop: threading.Event | None = None) -> None:
        assert release_download.wait(timeout=10.0)

    monkeypatch.setattr("harlequin.app.locate_tzdata", lambda: False)
    monkeypatch.setattr("harlequin.app.download_tzdata", stalled_download)
    monkeypatch.setattr("harlequin.app.TZDATA_WAIT_SECONDS", 0.25)

    messages: list[Message] = []
    try:
        async with app.run_test(message_hook=messages.append) as pilot:
            await wait_for_editor(pilot, app)
            app.post_message(QuerySubmitted(queries=["select 1 as foo"], limit=None))
            assert (await _await_results(app, pilot, messages)).errors == []
            assert not app._tzdata_ready.is_set()
    finally:
        release_download.set()


@pytest.mark.asyncio
async def test_quitting_abandons_a_download_in_flight(
    windows_app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quit sets the stop flag, so the download thread is not joined at exit."""
    app = windows_app
    download_started = threading.Event()
    stop_seen = threading.Event()

    def watching_download(stop: threading.Event | None = None) -> None:
        download_started.set()
        assert stop is not None
        assert stop.wait(timeout=10.0), "quit never set the stop flag"
        stop_seen.set()

    monkeypatch.setattr("harlequin.app.locate_tzdata", lambda: False)
    monkeypatch.setattr("harlequin.app.download_tzdata", watching_download)
    # the autouse cache stub takes no keywords, and this is the one test that
    # runs the real `action_quit`
    monkeypatch.setattr("harlequin.app.update_catalog_cache", lambda **_: None)

    async with app.run_test() as pilot:
        await wait_for_editor(pilot, app)
        assert download_started.wait(timeout=10.0)
        await app.action_quit()
    assert stop_seen.wait(timeout=10.0)


@pytest.mark.asyncio
async def test_a_failed_tzdata_download_is_a_warning(
    windows_app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A session with no timezone database is usable, so it is not an error
    modal and it does not stop a query from running."""
    app = windows_app

    def failed_download(stop: threading.Event | None = None) -> None:
        raise HarlequinTzDataError(msg="No network.", title="Harlequin Timezone Error")

    monkeypatch.setattr("harlequin.app.locate_tzdata", lambda: False)
    monkeypatch.setattr("harlequin.app.download_tzdata", failed_download)

    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_editor(pilot, app)
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
        assert (await _await_results(app, pilot, messages)).errors == []
