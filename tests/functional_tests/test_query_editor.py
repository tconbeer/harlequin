from __future__ import annotations

import sys
from typing import Awaitable, Callable, List

import pytest
from textual.message import Message
from textual.notifications import Notify
from textual.widgets.text_area import Selection

from harlequin import Harlequin
from harlequin.app import QuerySubmitted


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
async def test_no_tree_sitter(
    app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    import textual.document._syntax_aware_document
    import textual.widgets._text_area

    monkeypatch.setattr(textual.document._syntax_aware_document, "TREE_SITTER", False)
    monkeypatch.setattr(textual.widgets._text_area, "TREE_SITTER", False)

    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)

        while app.editor is None:
            await pilot.pause()

        assert app.editor is not None
        assert app.editor.text_input is not None
        app.editor.text = "select 1; select 2"

        assert not app.editor.text_input.is_syntax_aware

        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        await pilot.pause()
        await wait_for_workers(app)

        submitted_msg = next(
            iter(filter(lambda m: isinstance(m, QuerySubmitted), messages))
        )
        assert submitted_msg
        assert isinstance(submitted_msg, QuerySubmitted)
        assert len(submitted_msg.queries) == 2

        text_area_warning = next(
            iter(
                filter(
                    lambda m: (
                        isinstance(m, Notify) and m.notification.severity == "warning"
                    ),
                    messages,
                )
            )
        )
        assert text_area_warning


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
        assert app.editor._query_separators() == [(0, 25), (1, 27), (2, 18), (3, 15)]

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
