from __future__ import annotations

import sys
from pathlib import Path
from typing import Awaitable, Callable, List, Sequence

import pytest
from textual.app import App
from textual.widgets import TextArea
from textual.widgets.text_area import Selection
from textual.worker import WorkerFailed

from harlequin import Harlequin
from harlequin.autocomplete import BufferSymbols
from harlequin.autocomplete import find_symbols as real_find_symbols
from harlequin.components.code_editor import CodeEditor
from harlequin.components.text_modal import ErrorModal
from harlequin.statements import find_separators, split


@pytest.mark.asyncio
async def test_query_formatting(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select\n\n1 FROM\n\n foo"

        await pilot.press("f4")
        assert app.editor.text == "select 1 from foo\n"
        assert list(app._notifications)[-1].message == "Formatted query."

        # formatting an already-formatted query notifies that nothing changed
        await pilot.press("f4")
        assert app.editor.text == "select 1 from foo\n"
        assert "no changes" in list(app._notifications)[-1].message


@pytest.mark.flaky
@pytest.mark.asyncio
async def test_multiple_buffers(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        assert app.editor_collection
        assert app.editor_collection.tab_count == 1
        assert app.editor_collection.active == "tab-1"
        app.editor.text = "tab 1"
        await pilot.press("home")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Tab 1 of 1 (No tabs)"))

        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 2
        assert app.editor_collection.active == "tab-2"
        assert app.editor.text == ""
        app.editor.text = "tab 2"
        await pilot.press("home")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Tab 2 of 2"))

        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == ""
        app.editor.text = "tab 3"
        await pilot.press("home")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Tab 3 of 3"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-1"
        assert app.editor.text == "tab 1"
        snap_results.append(await app_snapshot(app, "Tab 1 of 3"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-2"
        assert app.editor.text == "tab 2"
        snap_results.append(await app_snapshot(app, "Tab 2 of 3"))

        await pilot.press("ctrl+w")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 2
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == "tab 3"
        # TODO: bring back this flaky test.
        # snap_results.append(await app_snapshot(app, "Tab 3 after deleting 2"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.active == "tab-1"
        assert app.editor.text == "tab 1"
        snap_results.append(await app_snapshot(app, "Tab 1 of [1,3]"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == "tab 3"
        snap_results.append(await app_snapshot(app, "Tab 3 of [1,3]"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_buffers_keep_their_state(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Switching buffers has to carry everything the editor holds, not just the text."""
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        app.editor.focus()
        await pilot.press(*"select 1")
        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.press(*"select 2")
        await pilot.press("ctrl+k")
        await pilot.pause()

        assert app.editor_collection.active == "tab-1"
        assert app.editor.text == "select 1"
        assert app.editor.selection == Selection((0, 8), (0, 8))

        # the undo history moves with the buffer: this undoes the typing
        # in buffer one, not the switch that loaded it.
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert app.editor.text == ""
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert app.editor.text == ""

        await pilot.press("ctrl+k")
        await pilot.pause()
        assert app.editor_collection.active == "tab-2"
        assert app.editor.text == "select 2"

        # a buffer scrolled away from its cursor comes back where it was,
        # instead of snapping to the cursor.
        assert app.editor.text_input is not None
        app.editor.text = "\n".join(f"select {i}" for i in range(100))
        await pilot.press("ctrl+down", "ctrl+down", "ctrl+down")
        await pilot.pause()
        scrolled_to = app.editor.text_input.scroll_offset
        assert scrolled_to.y > 0
        assert app.editor.selection == Selection((0, 0), (0, 0))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert app.editor_collection.active == "tab-2"
        assert app.editor.text_input.scroll_offset == scrolled_to


@pytest.mark.flaky
@pytest.mark.asyncio
async def test_word_autocomplete(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    transaction_button_visible: Callable[[Harlequin], bool],
) -> None:
    app = app_all_adapters
    snap_results: List[bool] = []
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None or app.editor_collection.word_completer is None:
            await pilot.pause()

        # we need to let the data catalog load the root's children
        while (
            app.data_catalog.database_tree.loading
            or not app.data_catalog.database_tree.root.children
        ):
            await pilot.pause()

        app.editor.focus()

        await pilot.press("s")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "s"))

        await pilot.press("e")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "se"))

        await pilot.press("l")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "sel"))

        await pilot.press("backspace")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "se again"))

        await pilot.press("l")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        await pilot.press("enter")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "submitted"))

        if not (transaction_button_visible(app)):
            assert all(snap_results)


@pytest.mark.skipif(
    sys.platform == "win32", reason="Initial snapshot very flaky on windows."
)
@pytest.mark.flaky
@pytest.mark.asyncio
async def test_member_autocomplete(
    app_small_duck: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    expand_catalog_node: Callable[..., Awaitable[None]],
) -> None:
    app = app_small_duck
    snap_results: List[bool] = []
    async with app.run_test() as pilot:
        await wait_for_workers(app)

        # we need to expand the data catalog to load items into the completer
        while (
            app.data_catalog.database_tree.loading
            or not app.data_catalog.database_tree.root.children
        ):
            await pilot.pause()
        for db_node in app.data_catalog.database_tree.root.children:
            await expand_catalog_node(pilot, db_node)
            await wait_for_workers(app)
            for schema_node in db_node.children:
                await expand_catalog_node(pilot, schema_node)
                await wait_for_workers(app)
        # the relations are on screen now, so the catalog prefetches their
        # columns; wait for those to reach the member completer.
        await pilot.pause(1)

        # now the completer should be populated
        while app.editor is None or app.editor_collection.member_completer is None:
            await pilot.pause()

        app.editor.text = '"drivers"'
        app.editor.selection = Selection((0, 9), (0, 9))
        app.editor.focus()

        await pilot.press("full_stop")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "driver members"))

        await pilot.press("quotation_mark")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "with quote"))

        await pilot.press("enter")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "submitted"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_footer_inputs(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    app_snapshot: Callable[..., Awaitable[bool]],
) -> None:
    snap_results: List[bool] = []
    async with app.run_test() as pilot:
        await wait_for_workers(app)

        while app.editor is None:
            await pilot.pause()

        assert app.editor is not None
        assert app.editor.text_input is not None
        app.editor.text = "select 1"

        await pilot.press("ctrl+o")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Open Input visible"))

        await pilot.press("ctrl+s")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Save Input visible"))

        await pilot.press("escape")
        await pilot.pause(0.1)
        snap_results.append(await app_snapshot(app, "No Input visible"))

        await pilot.press("ctrl+f")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Find Input visible"))

        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("ctrl+g")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Goto Input visible"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_selected_queries(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Regression test for #929: the queries at the cursor or selection are
    returned once each, in the order they appear in the buffer."""
    create = "create table foo (a int);"
    insert = "insert into foo values (1);"
    select = "select * from foo;"
    drop = "drop table foo;"
    async with app.run_test() as pilot:
        await wait_for_workers(app)

        while app.editor is None:
            await pilot.pause()

        assert app.editor.text_input is not None
        assert app.editor.text_input.is_syntax_aware
        app.editor.text = f"{create}\n{insert}\n{select}\n{drop}\n"
        await pilot.pause()

        # tree-sitter does not capture the semicolons in buffer order
        assert find_separators(app.editor.text) == [
            (0, 25),
            (1, 27),
            (2, 18),
            (3, 15),
        ]

        # the whole buffer
        app.editor.selection = Selection((0, 0), (4, 0))
        assert app.editor.selected_queries() == [create, insert, select, drop]

        # a reverse selection is the same as a forward one
        app.editor.selection = Selection((4, 0), (0, 0))
        assert app.editor.selected_queries() == [create, insert, select, drop]

        # a selection that partially covers two queries runs both of them
        app.editor.selection = Selection((1, 5), (2, 5))
        assert app.editor.selected_queries() == [insert, select]

        # a cursor inside a query runs only that query
        for row, query in enumerate([create, insert, select, drop]):
            app.editor.selection = Selection((row, 3), (row, 3))
            assert app.editor.selected_queries() == [query]

        # a cursor just after a semicolon runs the query it terminates,
        # not the one that follows it
        app.editor.selection = Selection((1, 27), (1, 27))
        assert app.editor.selected_queries() == [insert]

        # a cursor at the start of a query runs that query
        app.editor.selection = Selection((2, 0), (2, 0))
        assert app.editor.selected_queries() == [select]

        # a cursor in the trailing whitespace runs the last query
        app.editor.selection = Selection((4, 0), (4, 0))
        assert app.editor.selected_queries() == [drop]

        # a semicolon in a string literal does not separate queries
        app.editor.text = "select 'a;b'"
        await pilot.pause()
        app.editor.selection = Selection((0, 0), (0, 0))
        assert app.editor.selected_queries() == ["select 'a;b'"]


@pytest.mark.asyncio
async def test_symbol_scan_failure_warns_once_per_failure_streak(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed symbol scan warns instead of crashing the app.

    Regression test for #1117: read_symbols used Textual's default
    exit_on_error=True, so a buffer the scanner couldn't parse (or any bug in
    the scanner) took the whole app down with it. The failure warns once per
    streak -- repeated failures on the same buffer stay quiet until a scan
    succeeds again.
    """
    WARNING = "Harlequin could not read this buffer's identifiers."
    outcomes: list[str] = ["fail", "fail", "ok", "fail"]
    # the editor schedules a scan of the buffer shortly after mounting; it
    # would cancel (exclusive group) whichever direct scan below it landed on,
    # so drop that handler from the registry before the app mounts.
    monkeypatch.setitem(CodeEditor._decorated_handlers, TextArea.Changed, [])

    def flaky_scan(text: str) -> BufferSymbols:
        outcome = outcomes.pop(0)
        if outcome == "fail":
            raise RuntimeError("symbol scan boom")
        return real_find_symbols(text)

    def warning_count() -> int:
        return len(
            [
                notification
                for notification in list(app._notifications)
                if notification.severity == "warning"
                and notification.message == WARNING
            ]
        )

    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        editor = app.editor
        assert editor is not None
        monkeypatch.setattr("harlequin.components.code_editor.find_symbols", flaky_scan)

        # the first failure of a streak warns ...
        with pytest.raises(WorkerFailed):
            await editor.read_symbols("select 1").wait()
        await pilot.pause()
        assert warning_count() == 1

        # ... a repeated failure on the same buffer does not
        with pytest.raises(WorkerFailed):
            await editor.read_symbols("select 1").wait()
        await pilot.pause()
        assert warning_count() == 1

        # a successful scan resets the streak
        await editor.read_symbols("select 1").wait()
        for _ in range(20):
            if not editor._symbol_scan_failed:
                break
            await pilot.pause(0.05)
        assert not editor._symbol_scan_failed
        assert warning_count() == 1

        # so the next failure warns again
        with pytest.raises(WorkerFailed):
            await editor.read_symbols("select 1").wait()
        await pilot.pause()
        assert warning_count() == 2

        # and the app never saw an unhandled exception
        assert app._exception is None
        assert app.is_running


@pytest.mark.asyncio
async def test_selected_queries_split_on_character_columns(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Regression test for #1015: the editor and `harlequin.statements` split
    at the same place.

    tree-sitter reports columns in bytes, and the editor used to feed one
    straight to `get_text_range()`, which wants characters. Any non-ASCII
    before a semicolon shifted the cut, and both halves came out as syntax
    errors -- this split into "select '日本語';select" and "2".
    """
    async with app.run_test() as pilot:
        await wait_for_workers(app)

        while app.editor is None:
            await pilot.pause()

        app.editor.text = "select '日本語';select 2"
        await pilot.pause()
        app.editor.selection = Selection((0, 0), (0, 22))
        assert app.editor.selected_queries() == ["select '日本語';", "select 2"]
        assert app.editor.selected_queries() == [s.sql for s in split(app.editor.text)]


@pytest.mark.asyncio
async def test_selected_queries_do_not_split_dollar_quoted_bodies(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Regression test for #1019: the editor and `harlequin.statements` agree,
    and neither treats a semicolon inside `$$ ... $$` as a separator."""
    async with app.run_test() as pilot:
        await wait_for_workers(app)

        while app.editor is None:
            await pilot.pause()

        script = "create function f() as $$ select 1; $$; select 2"
        app.editor.text = script
        await pilot.pause()
        app.editor.selection = Selection((0, 0), (0, len(script)))
        assert app.editor.selected_queries() == [
            "create function f() as $$ select 1; $$;",
            "select 2",
        ]
        assert app.editor.selected_queries() == [s.sql for s in split(app.editor.text)]


@pytest.mark.asyncio
async def test_buffer_symbols_reach_the_completers(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None or app.editor_collection.word_completer is None:
            await pilot.pause()
        word_completer = app.editor_collection.word_completer
        member_completer = app.editor_collection.member_completer
        assert member_completer is not None

        assert not word_completer("my_c")
        assert not member_completer("t.my_c")

        app.editor.text = (
            "with my_cte as (select 1 as my_col) select t.my_col from my_cte t"
        )

        # the editor re-reads the buffer on a timer
        for _ in range(20):
            if word_completer("my_c"):
                break
            await pilot.pause(0.1)

        assert word_completer("my_c")[0] == (("my_cte", "buf"), "my_cte")
        assert member_completer("t.my_c") == [(("t.my_col", "buf"), "t.my_col")]

        # symbols belong to the buffer they were read from, so switching tabs
        # away from that query drops them, and switching back brings them back.
        first_buffer_id = app.editor_collection.active
        assert first_buffer_id is not None
        await app.editor_collection.action_new_buffer()
        for _ in range(20):
            if not word_completer("my_c"):
                break
            await pilot.pause(0.1)

        assert not word_completer("my_c")

        app.editor_collection.tabs.active = first_buffer_id
        for _ in range(20):
            if word_completer("my_c"):
                break
            await pilot.pause(0.1)

        assert word_completer("my_c")[0] == (("my_cte", "buf"), "my_cte")


@pytest.mark.asyncio
async def test_numbers_do_not_open_the_completion_list(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A number leaves the list closed, so enter inserts a newline."""
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None or app.editor_collection.word_completer is None:
            await pilot.pause()

        app.editor_collection.word_completer.update_buffer_symbols(
            BufferSymbols(names=("foo_1",))
        )
        app.editor.focus()

        await pilot.press("1")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        assert not app.editor.completion_list.is_open

        await pilot.press("enter")
        await pilot.pause()
        assert app.editor.text == "1\n"


@pytest.mark.asyncio
async def test_external_editor_round_trip(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The buffer goes out to $EDITOR as a file and comes back as one edit.

    The suspend is patched out: the headless driver cannot suspend, so
    run_in_terminal is the seam that stands in for a human's editor.
    """
    monkeypatch.setenv("EDITOR", "ed")
    seen_text: list[str] = []

    def fake_run_in_terminal(app: App, argv: Sequence[str]) -> int:
        path = Path(argv[-1])
        seen_text.append(path.read_text(encoding="utf-8"))
        path.write_text("select 2", encoding="utf-8")
        return 0

    monkeypatch.setattr("harlequin.external.run_in_terminal", fake_run_in_terminal)

    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select 1"
        app.editor.focus()

        await pilot.press("f7")
        await pilot.pause()

        assert seen_text == ["select 1"]
        assert app.editor.text == "select 2"

        # the round trip is one undo away
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert app.editor.text == "select 1"


@pytest.mark.asyncio
async def test_external_editor_nonzero_exit_discards_the_edit(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDITOR", "ed")

    def fake_run_in_terminal(app: App, argv: Sequence[str]) -> int:
        Path(argv[-1]).write_text("select 2", encoding="utf-8")
        return 1

    monkeypatch.setattr("harlequin.external.run_in_terminal", fake_run_in_terminal)

    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select 1"
        app.editor.focus()

        await pilot.press("f7")
        await pilot.pause()

        assert app.editor.text == "select 1"
        notification = list(app._notifications)[-1]
        assert "status 1" in notification.message
        assert notification.severity == "warning"


@pytest.mark.asyncio
async def test_external_editor_without_an_editor_named(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)

    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select 1"
        app.editor.focus()

        await pilot.press("f7")
        await pilot.pause()

        assert isinstance(app.screen, ErrorModal)
        assert "$EDITOR" in app.screen.text
        assert app.editor.text == "select 1"


@pytest.mark.asyncio
async def test_external_editor_in_a_terminal_that_cannot_suspend(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real suspend, which the headless driver refuses, is an error modal."""
    monkeypatch.setenv("EDITOR", sys.executable)

    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select 1"
        app.editor.focus()

        await pilot.press("f7")
        await pilot.pause()

        assert isinstance(app.screen, ErrorModal)
        assert "suspend" in app.screen.text
        assert app.editor.text == "select 1"
        assert app.is_running
