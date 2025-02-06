from __future__ import annotations

import sys
from datetime import date, datetime
from typing import Awaitable, Callable
from unittest.mock import MagicMock

import pytest
from textual.message import Message
from textual_fastdatatable import DataTable

from harlequin import Harlequin
from harlequin.components.results_viewer import ResultsViewer


def transaction_button_visible(app: Harlequin) -> bool:
    """
    Skip snapshot checks for versions of that app showing the autocommit button.
    """
    return sys.version_info >= (3, 12) and "Sqlite" in app.adapter.__class__.__name__


@pytest.mark.asyncio
async def test_dupe_column_names(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = app_all_adapters
    query = "select 1 as a, 1 as a, 2 as a, 2 as a"
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        if not transaction_button_visible(app):
            assert await app_snapshot(app, "dupe columns")


@pytest.mark.asyncio
async def test_copy_data(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_pyperclip: MagicMock,
) -> None:
    app = app_all_adapters
    query = "select 3, 'rosberg', 6, 'ROS', 'Nico', 'Rosberg', '1985-06-27', 'German', 'http://en.wikipedia.org/wiki/Nico_Rosberg'"
    expected = "3	rosberg	6	ROS	Nico	Rosberg	1985-06-27	German	http://en.wikipedia.org/wiki/Nico_Rosberg"
    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        assert app.results_viewer._has_focus_within
        keys = ["shift+right"] * 8
        await pilot.press(*keys)
        await pilot.wait_for_scheduled_animations()
        await pilot.press("ctrl+c")
        await pilot.pause()

        copied_message = list(
            filter(lambda m: isinstance(m, DataTable.SelectionCopied), messages)
        )[0]
        assert isinstance(copied_message, DataTable.SelectionCopied)
        assert isinstance(copied_message.values, list)

        app.editor.text = ""
        app.editor.focus()
        await pilot.press("ctrl+v")  # paste
        assert app.editor.text == expected
        if not transaction_button_visible(app):
            assert await app_snapshot(app, "paste values from table")


@pytest.mark.asyncio
async def test_data_truncated_with_tooltip(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = app_all_adapters
    query = "select 'supercalifragilisticexpialidocious'"
    async with app.run_test(tooltips=True) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        await pilot.hover(ResultsViewer, (2, 2))
        await pilot.pause(0.5)
        if not transaction_button_visible(app):
            assert await app_snapshot(app, "hover over truncated value")


@pytest.mark.asyncio
async def test_infinity_timestamp(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    query = """
        select
            'infinity'::date,
            'infinity'::timestamp,
            '-infinity'::date,
            '-infinity'::timestamp
        """
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()

        results_table = app.results_viewer.get_visible_table()
        assert results_table is not None
        assert results_table.get_row_at(0) == [
            date.max,
            datetime.max,
            date.min,
            datetime.min,
        ]

        assert await app_snapshot(app, "hover over truncated value")
