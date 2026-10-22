from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import pytest
from textual.command import Command, CommandList, CommandPalette
from textual.widgets.text_area import Selection

from harlequin import Harlequin
from harlequin.components.code_editor import CodeEditor
from tests.functional_tests.helpers import wait_for_editor
from tests.waiting import wait_for


@pytest.mark.asyncio
async def test_close_and_reopen_preserves_selection_and_undo(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection
        await pilot.press(*"select 1")
        selection = editor.selection
        await pilot.press("ctrl+w")
        assert editor.text == ""
        assert collection.tab_count == 1
        assert [state.text for state in collection.closed_buffer_states] == ["select 1"]

        await pilot.press("ctrl+shift+t")
        assert editor is collection.current_editor
        assert len(app.query(CodeEditor)) == 1
        assert editor.text == "select 1"
        assert editor.selection == selection
        assert collection.tab_count == 2
        assert not collection.closed_buffer_states
        await pilot.press("ctrl+z")
        assert editor.text == ""
        await pilot.press("ctrl+w", "ctrl+shift+t")
        assert editor.text == ""
        assert collection.tab_count == 1
        assert not collection.closed_buffer_states


@pytest.mark.asyncio
async def test_reopen_preserves_scroll_away_from_cursor(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        assert editor.text_input is not None
        editor.text = "\n".join(f"select {index}" for index in range(100))
        await pilot.press("ctrl+down", "ctrl+down", "ctrl+down")
        scroll_offset = editor.text_input.scroll_offset
        assert scroll_offset.y > 0
        assert editor.selection == Selection()
        await pilot.press("ctrl+w", "ctrl+shift+t")
        assert editor.text_input.scroll_offset == scroll_offset
        assert editor.selection == Selection()


@pytest.mark.parametrize("text", ["", " \n\t"])
@pytest.mark.asyncio
async def test_empty_buffers_are_deleted(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    text: str,
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        editor.text = text
        await pilot.press("ctrl+w", "ctrl+shift+t", "ctrl+z")
        assert editor.text == ""
        assert app.editor_collection.tab_count == 1
        assert not app.editor_collection.closed_buffer_states


@pytest.mark.asyncio
async def test_close_saves_the_loaded_buffer_before_switching(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        editor.text = "first"
        await pilot.press("ctrl+n")
        await pilot.press(*"second")
        await pilot.press("ctrl+w")
        assert editor.text == "first"
        assert [state.text for state in app.editor_collection.closed_buffer_states] == [
            "second"
        ]
        await pilot.press("ctrl+shift+t")
        assert editor.text == "second"
        assert [buffer.text for buffer in app.editor_collection.buffers] == [
            "first",
            "second",
        ]


@pytest.mark.asyncio
async def test_close_all_retains_buffers_and_restores_active_one_first(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection
        editor.text = "already closed"
        await pilot.press("ctrl+w")
        for text in ("first", "second", "third"):
            editor.text = text
            await pilot.press("ctrl+n")
        collection.tabs.active = "tab-3"
        await wait_for(
            pilot, lambda: editor.text == "second", description="the second buffer"
        )

        await asyncio.gather(
            collection.action_close_all_buffers(),
            collection.action_close_all_buffers(),
        )
        assert collection.tab_count == 1
        assert editor.text == ""
        assert collection.has_class("hide-tabs")
        assert [state.text for state in collection.closed_buffer_states] == [
            "already closed",
            "first",
            "third",
            "second",
        ]
        for expected in ("second", "third", "first", "already closed"):
            await pilot.press("ctrl+shift+t")
            assert editor.text == expected
        assert not collection.closed_buffer_states
        assert collection.tab_count == 5


@pytest.mark.asyncio
async def test_picker_lists_literal_previews_and_restores_a_selected_buffer(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection
        for text in ("\n客户[status]\nselect 1", "a[/]", "客户[status]\nselect 2"):
            editor.text = text
            await pilot.press("ctrl+w")
        command = next(
            command
            for command in app.get_system_commands(app.screen)
            if command.title == "Closed Buffers"
        )
        await command.callback()
        await wait_for(
            pilot,
            lambda: (
                isinstance(app.screen, CommandPalette)
                and bool(app.screen.query(CommandList))
            ),
            description="the closed-buffer picker to mount",
        )
        command_list = app.screen.query_one(CommandList)
        await wait_for(
            pilot,
            lambda: command_list.option_count == 3,
            description="all three closed buffers",
        )
        options = [command_list.get_option_at_index(index) for index in range(3)]
        assert [str(option.prompt).splitlines()[0] for option in options] == [
            "客户[status]",
            "a[/]",
            "客户[status]",
        ]
        assert {
            option.hit.help for option in options if isinstance(option, Command)
        } == {"Closed buffer 1", "Closed buffer 2", "Closed buffer 3"}
        await pilot.press(*"a[/]")
        await wait_for(
            pilot,
            lambda: command_list.option_count == 1,
            description="the literal matching preview",
        )
        assert "a[/]" in str(command_list.get_option_at_index(0).prompt)
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: editor.text == "a[/]" and editor.has_focus_within,
            description="the selected buffer to reopen",
        )
        assert len(collection.closed_buffer_states) == 2

        collection.action_show_closed_buffers()
        await pilot.press("escape")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, CommandPalette),
            description="the picker to cancel",
        )
        assert editor.text == "a[/]"
        assert len(collection.closed_buffer_states) == 2


@pytest.mark.asyncio
async def test_a_closed_buffer_cannot_be_restored_twice(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection
        editor.text = "select 1"
        await pilot.press("ctrl+w")
        collection.action_show_closed_buffers()
        await wait_for(
            pilot,
            lambda: (
                isinstance(app.screen, CommandPalette)
                and bool(app.screen.query(CommandList))
            ),
            description="the closed-buffer picker to mount",
        )
        command_list = app.screen.query_one(CommandList)
        await wait_for(
            pilot,
            lambda: command_list.option_count == 1,
            description="the closed buffer",
        )
        state = collection.closed_buffer_states[-1]
        await asyncio.gather(
            collection._reopen_buffer(state), collection._reopen_buffer(state)
        )
        assert collection.tab_count == 2
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, CommandPalette),
            description="the stale picker to close",
        )
        assert collection.tab_count == 2
        assert editor.text == "select 1"
        assert not collection.closed_buffer_states


@pytest.mark.asyncio
async def test_reopen_actions_do_nothing_without_closed_buffers(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        assert not {"Closed Buffers", "Reopen Closed Buffer"} & {
            command.title for command in app.get_system_commands(app.screen)
        }
        app.editor_collection.action_show_closed_buffers()
        await pilot.press("ctrl+shift+t")
        assert not isinstance(app.screen, CommandPalette)
        assert editor.text == ""
        assert app.editor_collection.tab_count == 1
