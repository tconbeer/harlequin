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
from textual_textarea.text_editor import TextAreaPlus

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
from tests.functional_tests.helpers import wait_for_editor
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


async def settle_filter(
    pilot: Pilot,
    app: Harlequin,
    term: str,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Wait until the screen is listing what `term` matches.

    A poll rather than a sleep: the debounce arms a timer, so how long the read
    takes to start is not something a test can time.
    """
    screen = app.screen
    assert isinstance(screen, HistoryScreen)
    for _ in range(int(FILTER_INTERVAL * 100)):
        await pilot.pause(FILTER_INTERVAL / 4)
        await wait_for_workers(app)
        await pilot.pause()
        if screen.search == term:
            return
    raise AssertionError(f"the filter never settled on {term!r}")


async def type_filter(
    pilot: Pilot,
    app: Harlequin,
    term: str,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Type into the filter and wait for the read it debounces to land."""
    screen = app.screen
    assert isinstance(screen, HistoryScreen)
    screen.filter_input.focus()
    await pilot.pause()
    await pilot.press(*term)
    await settle_filter(pilot, app, term, wait_for_workers)


def preview_area(screen: HistoryScreen) -> TextAreaPlus:
    """The text area inside the preview, which is what focus and scroll act on."""
    area = screen.preview.text_input
    assert area is not None
    return area


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

        await type_filter(pilot, app, "select 1", wait_for_workers)
        snap_results.append(await app_snapshot(app, "Filtered History Viewer"))

        await pilot.press("escape")
        await settle_filter(pilot, app, "", wait_for_workers)
        await pilot.press("escape")
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

        for key in ("f1", "f10", "f12"):
            await pilot.press(key)
            await pilot.pause()
            assert app.screen is screen, f"{key} pushed a screen"
        assert not [s for s in app.screen_stack if isinstance(s, HelpScreen)]
        assert not app.full_screen, "f10 reached the app"


LONG_QUERY = "select\n" + ",\n".join(f"  {i} as column_{i}" for i in range(60)) + "\n;"


async def focus_preview(
    pilot: Pilot,
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    query: str = LONG_QUERY,
) -> HistoryScreen:
    """Open the History screen over one query and tab into the preview."""
    app.post_message(QuerySubmitted(queries=[query], limit=None))
    await pilot.pause()
    await wait_for_workers(app)
    await pilot.pause()
    screen = await open_history(pilot, app, wait_for_workers)
    await pilot.press("tab")
    await pilot.pause()
    assert preview_area(screen).has_focus, "tab did not reach the preview"
    return screen


@pytest.mark.asyncio
async def test_the_preview_shows_no_cursor(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """https://github.com/tconbeer/harlequin/issues/850

    It takes focus so it can be scrolled, but a query it will not let anyone
    change does not get a cursor.
    """
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await focus_preview(pilot, app, wait_for_workers)

        assert not preview_area(screen)._draw_cursor
        assert not preview_area(screen)._has_cursor


@pytest.mark.asyncio
async def test_the_preview_scrolls_from_the_keyboard(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A query taller than the pane is readable without a mouse."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await focus_preview(pilot, app, wait_for_workers)
        text_area = preview_area(screen)
        assert text_area.scroll_offset.y == 0

        await pilot.press(*["down"] * 5)
        await pilot.pause()
        scrolled = text_area.scroll_offset.y
        assert scrolled > 0, "down did not scroll the preview"

        await pilot.press("pagedown")
        await pilot.pause()
        assert text_area.scroll_offset.y > scrolled, "pagedown did not scroll further"

        await pilot.press(*["up"] * 100)
        await pilot.pause()
        assert text_area.scroll_offset.y == 0


@pytest.mark.asyncio
async def test_the_preview_scrolls_sideways_from_the_keyboard(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A query on one very long line is read across, not down."""
    one_liner = "select '" + "lorem ipsum dolor sit amet " * 200 + "';"
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await focus_preview(pilot, app, wait_for_workers, query=one_liner)
        text_area = preview_area(screen)
        assert text_area.scroll_offset.x == 0

        await pilot.press(*["right"] * 5)
        await pilot.pause()
        assert text_area.scroll_offset.x > 0, "right did not scroll the preview"

        await pilot.press(*["left"] * 100)
        await pilot.pause()
        assert text_area.scroll_offset.x == 0


@pytest.mark.asyncio
async def test_the_preview_takes_no_input(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Focus makes it scrollable, not editable.

    Every key the text area still binds, rather than a list written here: a
    read-only text area edits anyway under `paste`, `cut`, `undo` and friends,
    which reach the document without a keypress
    (https://github.com/tconbeer/textual-textarea/issues/346), and the
    `EDITING_ACTIONS` guarding against that has to be told when it goes stale.
    """
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await focus_preview(pilot, app, wait_for_workers)
        text_area = preview_area(screen)
        # a machine whose clipboard has something on it, as CI runners do
        monkeypatch.setattr(text_area, "system_paste", lambda: "PASTED")
        text_area.clipboard = "PASTED"

        edited_by = []
        for key in sorted(set(text_area._bindings.key_to_bindings)):
            await pilot.press(key)
            await pilot.pause()
            await pilot.pause()
            if screen.preview.text != LONG_QUERY:
                edited_by.append(key)
                screen.preview.text = LONG_QUERY
                await pilot.pause()
        assert not edited_by, f"these keys edited a read-only preview: {edited_by}"

        await pilot.press("x", "backspace", "delete")
        await pilot.pause()
        assert screen.preview.text == LONG_QUERY


@pytest.mark.asyncio
async def test_the_previews_own_editor_keys_are_not_live(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Nothing in the preview is saved, opened or searched."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await focus_preview(pilot, app, wait_for_workers)

        editor_keys = {"ctrl+s", "ctrl+o", "ctrl+f", "f3", "ctrl+g"}
        assert not editor_keys & set(app.screen.active_bindings)

        for key in sorted(editor_keys):
            await pilot.press(key)
            await pilot.pause()
            assert app.is_running, f"{key} closed the app"
            assert app.screen is screen, f"{key} pushed a screen"


@pytest.mark.asyncio
async def test_quit_still_reaches_the_app_from_the_screen(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The modal stops the app's bindings, but quit is a priority binding."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        await open_history(pilot, app, wait_for_workers)

        await pilot.press("ctrl+q")
        await pilot.pause()
        assert not app.is_running


@pytest.mark.asyncio
async def test_enter_selects_a_query_from_the_preview(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The screen's key wins wherever focus is."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await focus_preview(pilot, app, wait_for_workers)

        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is not screen
        assert app.editor is not None
        assert app.editor.text == LONG_QUERY


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
async def test_an_edit_that_changes_nothing_keeps_your_place(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The debounce reads once per interval, whatever was typed inside it.

    A typo and its correction, or a term every match already contains, both
    arrive here as a read for the term already listed.
    """
    log = QueryLog(program="hsql", connection="foo")
    for i in range(5):
        log.write(f"select {i} from orders")
    log.close()

    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)
        await type_filter(pilot, app, "orders", wait_for_workers)

        # enter takes the term to the list, where the arrows move the highlight
        await pilot.press("enter", "down", "down")
        await pilot.pause()
        assert screen.list.highlighted == 2
        chosen = screen.preview.text

        # the read the debounce will make for a term already listed -- watched,
        # because waiting on the term settling would be waiting on what is
        # already true
        reads: list[str] = []
        read_history = screen.read_history

        def watched_read(search: str) -> None:
            reads.append(search)
            read_history(search)

        screen.read_history = watched_read  # type: ignore[assignment,method-assign]

        # typed and taken back inside one debounce window
        screen.filter_input.focus()
        await pilot.pause()
        await pilot.press("x", "backspace")
        for _ in range(int(FILTER_INTERVAL * 100)):
            await pilot.pause(FILTER_INTERVAL / 4)
            await wait_for_workers(app)
            await pilot.pause()
            if reads:
                break
        assert reads == ["orders"], "the debounce never re-read the unchanged term"

        assert screen.filter_input.value == "orders"
        assert screen.list.highlighted == 2, "the list jumped back to the top"
        assert screen.preview.text == chosen


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
        assert len(screen.history) == 1, "the record the filter has to miss is absent"

        await type_filter(pilot, app, "line_items", wait_for_workers)
        assert len(screen.history) == 0


@pytest.mark.asyncio
async def test_the_screen_opens_on_the_list(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Picking a query is what the screen is for, so the list has focus."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        app.post_message(QuerySubmitted(queries=["select 1;"], limit=None))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)

        assert screen.list.has_focus


@pytest.mark.asyncio
async def test_tab_cycles_the_three_panes(
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
        assert screen.list.has_focus

        await pilot.press("tab")
        await pilot.pause()
        assert preview_area(screen).has_focus

        await pilot.press("tab")
        await pilot.pause()
        assert screen.filter_input.has_focus

        await pilot.press("tab")
        await pilot.pause()
        assert screen.list.has_focus


@pytest.mark.asyncio
async def test_typing_over_the_list_starts_a_search(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A key the list has no use for is the start of a filter term."""
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
        assert screen.list.has_focus

        await pilot.press("o")
        await pilot.pause()
        assert screen.filter_input.has_focus, "typing did not reach the filter"
        assert screen.filter_input.value == "o", "the first key typed was lost"

        await pilot.press("r", "d", "e", "r", "s")
        await settle_filter(pilot, app, "orders", wait_for_workers)
        assert screen.filter_input.value == "orders"
        assert [record.query_text for record in screen.history] == [
            "select 2 as orders;"
        ]


@pytest.mark.asyncio
async def test_the_arrow_keys_do_not_reach_the_list_from_the_filter(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The filter is a text box, not a second set of keys for the list."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        app.post_message(
            QuerySubmitted(queries=["select 1;", "select 2;", "select 3;"], limit=None)
        )
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        screen = await open_history(pilot, app, wait_for_workers)
        screen.filter_input.focus()
        await pilot.pause()
        highlighted = screen.list.highlighted

        await pilot.press("down", "down", "pagedown", "up", "pageup")
        await pilot.pause()
        assert screen.list.highlighted == highlighted


@pytest.mark.asyncio
async def test_escape_empties_the_filter_then_leaves_it(
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
        await settle_filter(pilot, app, "", wait_for_workers)
        assert app.screen is screen
        assert screen.filter_input.value == ""
        assert screen.filter_input.has_focus, "an emptied filter also lost focus"
        assert [record.query_text for record in screen.history] == ["select 1;"]

        # empty, so the second one hands the screen back to the list
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen
        assert screen.list.has_focus

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen


@pytest.mark.asyncio
async def test_escape_over_the_preview_leaves_the_screen(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Only the filter gives escape a second meaning."""
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        screen = await focus_preview(pilot, app, wait_for_workers)

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen


@pytest.mark.asyncio
async def test_enter_in_the_filter_goes_to_the_list(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Enter commits the search rather than the query, so the list can be driven."""
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
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is screen, "enter in the filter left the screen"
        assert screen.list.has_focus
        assert screen.filter_input.value == "orders", "the term was cleared"

        # and enter again, over the list, takes the query
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is not screen
        assert app.editor is not None
        assert app.editor.text == "select 2 as orders;"
