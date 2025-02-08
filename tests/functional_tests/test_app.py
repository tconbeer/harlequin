from __future__ import annotations

import sys
from typing import Awaitable, Callable

import pytest
from textual.message import Message

from harlequin import Harlequin
from harlequin.app import QueriesExecuted, QuerySubmitted, ResultsFetched
from harlequin.components import ErrorModal


def transaction_button_visible(app: Harlequin) -> bool:
    """
    Skip snapshot checks for versions of that app showing the autocommit button.
    """
    return sys.version_info >= (3, 12) and "Sqlite" in app.adapter.__class__.__name__


@pytest.mark.asyncio
async def test_select_1(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = app_all_adapters
    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        assert app.title == "Harlequin"
        assert app.focused.__class__.__name__ == "TextAreaPlus"

        q = "select 1 as foo"
        for key in q:
            await pilot.press(key)
        await pilot.press("ctrl+j")  # alias for ctrl+enter

        await pilot.pause()
        [query_submitted_message] = [
            m for m in messages if isinstance(m, QuerySubmitted)
        ]
        assert query_submitted_message.queries == [q]
        await wait_for_workers(app)
        await pilot.pause()
        [query_executed_message] = [
            m for m in messages if isinstance(m, QueriesExecuted)
        ]
        assert query_executed_message.query_count == 1
        assert query_executed_message.cursors
        await wait_for_workers(app)
        await pilot.pause()
        [results_fetched_message] = [
            m for m in messages if isinstance(m, ResultsFetched)
        ]
        assert results_fetched_message.errors == []
        table = app.results_viewer.get_visible_table()
        assert table
        assert table.source_row_count == table.row_count == 1
        # sqlite on py3.12 will show the Tx: Auto button, and snap
        # will fail
        if not transaction_button_visible(app):
            assert await app_snapshot(app, "select 1 as foo")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "select 1+1",
        "select 'a' as foo",
        "select null",
        "select null as foo",
        "",
        "select 1 where false",
        "select 1 union all select 'hi'",
        "select 'hi' union all select 1",
        """select '[/foo]' as "[/bar]" """.strip(),
    ],
)
async def test_queries_do_not_crash_all_adapters(
    app_all_adapters: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    query: str,
) -> None:
    app = app_all_adapters
    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await pilot.pause()

        if query:
            [query_submitted_message] = [
                m for m in messages if isinstance(m, QuerySubmitted)
            ]
            assert query_submitted_message.queries == [query]
            await wait_for_workers(app)
            await pilot.pause()
            [query_executed_message] = [
                m for m in messages if isinstance(m, QueriesExecuted)
            ]
            assert query_executed_message.cursors
        if query and query != "select 1 where false":
            await pilot.pause()
            await wait_for_workers(app)
            table = app.results_viewer.get_visible_table()
            assert table is not None
            assert table.row_count >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "SELECT {'x': 1, 'y': 2, 'z': 3}",  # struct
        # also a struct:
        "SELECT {'yes': 'duck', 'maybe': 'goose', 'huh': NULL, 'no': 'heron'}",
        "SELECT {'key1': 'string', 'key2': 1, 'key3': 12.345}",  # struct
        """SELECT {'birds':
            {'yes': 'duck', 'maybe': 'goose', 'huh': NULL, 'no': 'heron'},
        'aliens':
            NULL} as bar""",  # struct
        "select {'a': 5} union all select {'a': 6}",  # struct
        "select map {'a': 5}",  # map
        "select map {'a': 5} union all select map {'b': 6}",  # map
        "SELECT map { 1: 42.001, 5: -32.1 }",  # map
        "SELECT map { ['a', 'b']: [1.1, 2.2], ['c', 'd']: [3.3, 4.4] }",  # map
        "SELECT [1, 2, 3]",  # list
        "SELECT ['duck', 'goose', NULL, 'heron'];",  # list
        "SELECT [['duck', 'goose', 'heron'], NULL, ['frog', 'toad'], []];",  # list
        "set timezone = 'America/New_York'; select '2024-01-01'::timestamptz;",
        "set timezone = 'America/New_York'; select '1-1-1T00:00:00Z'::timestamptz",
    ],
)
async def test_queries_do_not_crash(
    app: Harlequin,
    query: str,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        table = app.results_viewer.get_visible_table()
        assert table is not None
        assert table.row_count >= 1


@pytest.mark.asyncio
async def test_multiple_queries(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = app_all_adapters
    snap_results: list[bool] = []
    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        q = "select 1; select 2"
        app.editor.text = q
        await pilot.press("ctrl+j")

        # should only run one query
        await wait_for_workers(app)
        await pilot.pause()
        [query_submitted_message] = [
            m for m in messages if isinstance(m, QuerySubmitted)
        ]
        assert query_submitted_message.queries == ["select 1;"]
        table = app.results_viewer.get_visible_table()
        assert table
        assert table.row_count == table.source_row_count == 1
        assert "hide-tabs" in app.results_viewer.classes
        await wait_for_workers(app)
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "One query"))

        app.editor.focus()
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        # should run both queries
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        [_, query_submitted_message] = [
            m for m in messages if isinstance(m, QuerySubmitted)
        ]
        assert query_submitted_message.queries == ["select 1;", "select 2"]
        assert app.results_viewer.tab_count == 2
        assert "hide-tabs" not in app.results_viewer.classes
        await wait_for_workers(app)
        await pilot.pause(0.5)
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "Both queries"))
        assert app.results_viewer.active == "result-1"
        await pilot.press("k")
        await pilot.wait_for_scheduled_animations()
        assert app.results_viewer.active == "result-2"
        snap_results.append(await app_snapshot(app, "Both queries, tab 2"))
        await pilot.press("k")
        await pilot.wait_for_scheduled_animations()
        assert app.results_viewer.active == "result-1"
        snap_results.append(await app_snapshot(app, "Both queries, tab 1"))
        await pilot.press("j")
        assert app.results_viewer.active == "result-2"
        await pilot.press("j")
        assert app.results_viewer.active == "result-1"

        if not transaction_button_visible(app):
            assert all(snap_results)


@pytest.mark.asyncio
async def test_single_query_terminated_with_semicolon(
    app_all_adapters: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = app_all_adapters
    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        q = "select 1;    \n\t\n"
        app.editor.text = q
        await pilot.press("ctrl+j")

        # should only run current query
        await wait_for_workers(app)
        await pilot.pause()
        [query_submitted_message] = [
            m for m in messages if isinstance(m, QuerySubmitted)
        ]
        assert query_submitted_message.queries == ["select 1;"]
        assert app.results_viewer.tab_count == 1

        app.editor.focus()
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")

        # should not run whitespace query, even though included
        # in selection.
        await wait_for_workers(app)
        await pilot.pause()
        [_, query_submitted_message] = [
            m for m in messages if isinstance(m, QuerySubmitted)
        ]
        assert query_submitted_message.queries == ["select 1;"]
        assert app.results_viewer.tab_count == 1

        app.editor.focus()
        await pilot.press("ctrl+end")
        await pilot.press("ctrl+j")
        # should run previous query
        assert not app.editor.current_query
        await wait_for_workers(app)
        await pilot.pause()
        [*_, query_submitted_message] = [
            m for m in messages if isinstance(m, QuerySubmitted)
        ]
        assert query_submitted_message.queries == ["select 1;"]
        assert app.results_viewer.tab_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_query",
    [
        "select",  # errors when building cursor
        "select 0::struct(id int)",  # errors when fetching data
        "select; select 0::struct(id int)",  # multiple errors
        "select 1; select 0::struct(id int)",  # one error, mult queries
        "select 0::struct(id int); select 1",  # one error, mult queries, err first
    ],
)
async def test_query_errors(
    app_all_adapters: Harlequin,
    bad_query: str,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = app_all_adapters
    snap_results: list[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = bad_query

        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert isinstance(app.screen, ErrorModal)
        snap_results.append(await app_snapshot(app, "Error visible"))

        await pilot.press("space")
        assert len(app.screen_stack) == 1

        # data table and query bar should be responsive
        assert "non-responsive" not in app.run_query_bar.classes
        assert "non-responsive" not in app.results_viewer.classes
        snap_results.append(await app_snapshot(app, "After dismissing error"))

        if not transaction_button_visible(app):
            assert all(snap_results)


@pytest.mark.asyncio
async def test_rich_markup(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause(0.1)

        q = "select '[some text]', '[red]some text[/]'"
        app.editor.text = q
        await pilot.press("ctrl+j")  # alias for ctrl+enter

        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        assert await app_snapshot(app, "select markup")
