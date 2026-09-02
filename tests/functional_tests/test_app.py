from __future__ import annotations

from types import SimpleNamespace
from typing import Awaitable, Callable, cast

import pytest
from textual.message import Message
from textual.worker import Worker, WorkerState

from harlequin import Harlequin
from harlequin.app import QueriesExecuted, QuerySubmitted, ResultsFetched
from harlequin.components import ErrorModal


@pytest.mark.asyncio
async def test_select_1(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    transaction_button_visible: Callable[[Harlequin], bool],
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
    transaction_button_visible: Callable[[Harlequin], bool],
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
    transaction_button_visible: Callable[[Harlequin], bool],
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


@pytest.mark.asyncio
async def test_adapter_raises_unexpected_error(
    app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """
    An adapter that raises a raw driver exception instead of a
    HarlequinQueryError should not crash the app.
    """
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        assert app.connection is not None

        def raise_driver_error(query: str) -> None:
            raise RuntimeError("1049 (42000): Unknown database 'ht;'")

        monkeypatch.setattr(app.connection, "execute", raise_driver_error)

        app.editor.text = "use ht;"
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()

        assert app.is_running
        assert isinstance(app.screen, ErrorModal)
        assert "Unknown database" in str(app.screen.error)

        await pilot.press("space")
        assert len(app.screen_stack) == 1
        assert "non-responsive" not in app.run_query_bar.classes
        assert "non-responsive" not in app.results_viewer.classes


def worker_error_message(
    worker_name: str, error: BaseException | None
) -> Worker.StateChanged:
    """A StateChanged ERROR the handler sees from a real failed worker.

    The message only carries the worker object; the handler reads its name and
    error, so a namespace stands in for the Worker.
    """
    return Worker.StateChanged(
        cast(
            "Worker",
            SimpleNamespace(name=worker_name, error=error),
        ),
        WorkerState.ERROR,
    )


def _modal_errors_on_screen(app: Harlequin) -> list[ErrorModal]:
    return [screen for screen in app.screen_stack if isinstance(screen, ErrorModal)]


@pytest.mark.asyncio
async def test_update_schema_data_worker_error_shows_catalog_modal(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        app.data_catalog.database_tree.loading = True
        await app.handle_worker_error(
            worker_error_message("update_schema_data", RuntimeError("boom"))
        )
        await pilot.pause()
        assert isinstance(app.screen, ErrorModal)
        assert app.screen.header == "Could not update data catalog"
        assert "boom" in str(app.screen.error)
        assert app.data_catalog.database_tree.loading is False


@pytest.mark.asyncio
async def test_connect_worker_error_exits_with_code_two(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        await app.handle_worker_error(
            worker_error_message("_connect", RuntimeError("connection refused"))
        )
        await pilot.pause()
    assert app.return_code == 2


@pytest.mark.asyncio
async def test_worker_error_from_unrecognized_worker_shows_modal(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A worker the handler doesn't name is loud by default, not silent.

    Regression test for #1117: errors from every unrecognized worker used to
    vanish without a trace.
    """
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        await app.handle_worker_error(
            worker_error_message("some_future_worker", RuntimeError("boom"))
        )
        await pilot.pause()
        assert app.is_running
        assert isinstance(app.screen, ErrorModal)
        assert "boom" in str(app.screen.error)


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_name", ["_execute_query", "_fetch_data"])
async def test_query_worker_error_shows_modal_and_restores_ui(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    worker_name: str,
) -> None:
    """A query worker that dies without posting its result message must not
    leave the run bar stuck in the non-responsive state."""
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        app.run_query_bar.set_not_responsive()
        app.results_viewer.show_loading()
        await app.handle_worker_error(
            worker_error_message(worker_name, RuntimeError("boom"))
        )
        await pilot.pause()
        assert app.is_running
        assert isinstance(app.screen, ErrorModal)
        assert "boom" in str(app.screen.error)
        assert "non-responsive" not in app.run_query_bar.classes
        assert "non-responsive" not in app.results_viewer.classes


@pytest.mark.asyncio
async def test_execute_query_worker_error_is_not_silent_and_app_survives(
    app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """An error in the code around _execute_query's per-statement handling
    shows a modal and restores the UI, instead of silently sticking it.

    Regression test for #1117, against the real worker machinery.
    """
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        assert app.connection is not None

        def broken_execute(
            connection: object,
            statements: object,
            limit: object = None,
            on_error: object = "stop",
        ) -> object:
            raise RuntimeError("boom inside execute core")

        monkeypatch.setattr("harlequin.app.execute", broken_execute)

        app.editor.text = "select 1"
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        for _ in range(100):
            if isinstance(app.screen, ErrorModal):
                break
            await pilot.pause(0.05)
        assert app.is_running
        assert isinstance(app.screen, ErrorModal)
        assert "boom inside execute core" in str(app.screen.error)
        assert "non-responsive" not in app.run_query_bar.classes

        await pilot.press("space")
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_toggle_transaction_mode_worker_error_shows_modal(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        await app.handle_worker_error(
            worker_error_message("toggle_transaction_mode", RuntimeError("boom"))
        )
        await pilot.pause()
        assert app.is_running
        assert isinstance(app.screen, ErrorModal)
        assert app.screen.title == "Transaction Error"
        assert "boom" in str(app.screen.error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("worker_name", "expected_message"),
    [
        (
            "_load_catalog_cache",
            "Harlequin could not load its cache; your query history may be missing.",
        ),
        ("_extend_and_merge_completers", "Harlequin could not update completions."),
        ("_build_completers", "Harlequin could not build completions."),
    ],
)
async def test_partial_failure_workers_notify_without_modal(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    worker_name: str,
    expected_message: str,
) -> None:
    """A worker whose failure leaves the app usable warns instead of modaling."""
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        await app.handle_worker_error(
            worker_error_message(worker_name, RuntimeError("boom"))
        )
        await pilot.pause()
        assert app.is_running
        assert not _modal_errors_on_screen(app)
        warnings = [
            notification
            for notification in list(app._notifications)
            if notification.severity == "warning"
        ]
        assert [n.message for n in warnings] == [expected_message]


@pytest.mark.asyncio
async def test_worker_errors_are_ignored_while_app_is_exiting(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """An error that lands during the exit drain is noise, not a modal."""
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        app._exit = True
        try:
            await app.handle_worker_error(
                worker_error_message("some_future_worker", RuntimeError("boom"))
            )
        finally:
            app._exit = False
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app.is_running


@pytest.mark.asyncio
async def test_worker_state_change_without_error_is_ignored(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        await app.handle_worker_error(worker_error_message("some_future_worker", None))
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app.is_running
