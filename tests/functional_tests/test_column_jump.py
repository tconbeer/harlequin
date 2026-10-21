from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from textual.command import Command, CommandList, CommandPalette, Hit
from textual.coordinate import Coordinate

from harlequin import Harlequin
from tests.functional_tests.helpers import wait_for_any_table, wait_for_editor
from tests.waiting import wait_for


@pytest.mark.asyncio
async def test_jump_to_column(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test(size=(80, 24)) as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        columns = [f"{index} as column_{index}" for index in range(15)]
        columns.append('15 as "客户[status]"')
        second_row = ", ".join(str(index) for index in range(16))
        editor.text = f"select {', '.join(columns)} union all select {second_row}"
        await pilot.press("ctrl+j")
        table = await wait_for_any_table(pilot, app)
        await wait_for(pilot, lambda: table.has_focus, description="results focus")
        table.move_cursor(row=1, column=0)
        table.move_cursor(column=1, select=True)
        await pilot.press("ctrl+g")
        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"客户[status]")
        command_list = app.screen.query_one(CommandList)
        await wait_for(
            pilot,
            lambda: command_list.option_count == 1,
            description="the matching column",
        )
        assert "客户[status]" in str(command_list.get_option_at_index(0).prompt)
        await pilot.press("enter")
        await wait_for(pilot, lambda: table.has_focus, description="results focus")
        assert table.cursor_coordinate == Coordinate(1, 15)
        assert table.selection_anchor_coordinate is None
        assert table.scroll_x > 0


@pytest.mark.asyncio
async def test_column_picker_duplicates_and_cancel(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test(size=(100, 30)) as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        editor.text = "select 1 as customer_name, 2 as customer_name, 3 as total"
        await pilot.press("ctrl+j")
        table = await wait_for_any_table(pilot, app)
        await wait_for(pilot, lambda: table.has_focus, description="results focus")
        await pilot.press("ctrl+g")
        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"CUSTNAME")
        command_list = app.screen.query_one(CommandList)
        await wait_for(
            pilot,
            lambda: command_list.option_count == 2,
            description="both matching duplicate columns",
        )
        options = [command_list.get_option_at_index(index) for index in range(2)]
        assert all(isinstance(option, Command) for option in options)
        assert {
            option.hit.help for option in options if isinstance(option, Command)
        } == {
            "Column 1",
            "Column 2",
        }
        await pilot.press("down", "enter")
        await wait_for(pilot, lambda: table.has_focus, description="results focus")
        assert table.cursor_coordinate == Coordinate(0, 1)

        await pilot.press("ctrl+g")
        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"no_such_column")
        command_list = app.screen.query_one(CommandList)
        await wait_for(
            pilot,
            lambda: command_list.option_count == 0,
            description="no matching columns",
        )
        await pilot.press("enter")
        assert isinstance(app.screen, CommandPalette)
        await pilot.press("escape")
        await wait_for(pilot, lambda: table.has_focus, description="results focus")
        assert table.cursor_coordinate == Coordinate(0, 1)


@pytest.mark.asyncio
async def test_column_picker_preserves_markup_labels(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        editor.text = 'select 1 as "a[/]", 2 as abc'
        await pilot.press("ctrl+j")
        table = await wait_for_any_table(pilot, app)
        await wait_for(pilot, lambda: table.has_focus, description="results focus")
        table.move_cursor(column=1)
        await pilot.press("ctrl+g")
        command_list = app.screen.query_one(CommandList)
        await wait_for(
            pilot,
            lambda: command_list.option_count == 2,
            description="both columns before searching",
        )
        assert "a[/]" in str(command_list.get_option_at_index(0).prompt)
        await pilot.press("a")
        await wait_for(
            pilot,
            lambda: (
                command_list.option_count == 2
                and all(
                    isinstance(option, Command) and isinstance(option.hit, Hit)
                    for option in (
                        command_list.get_option_at_index(index) for index in range(2)
                    )
                )
            ),
            description="both matching columns with literal markup",
        )
        options = [command_list.get_option_at_index(index) for index in range(2)]
        assert any("a[/]" in str(option.prompt) for option in options)
        assert any("abc" in str(option.prompt) for option in options)
        await pilot.press("enter")
        await wait_for(pilot, lambda: table.has_focus, description="results focus")
        assert table.cursor_coordinate == Coordinate(0, 0)


@pytest.mark.asyncio
async def test_jump_uses_active_result_and_palette(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        assert "Jump to Column" not in {
            command.title for command in app.get_system_commands(app.screen)
        }
        app.results_viewer.action_jump_to_column()
        assert not isinstance(app.screen, CommandPalette)

        editor.text = "select 1 as first_only; select 2 as other, 3 as second_only;"
        await pilot.press("ctrl+a", "ctrl+j")
        await wait_for(
            pilot,
            lambda: (
                app.results_viewer.tab_count == 2 and not app.results_viewer.loading
            ),
            description="both results",
        )
        app.results_viewer.action_switch_tab(1)
        table = await wait_for_any_table(pilot, app)
        assert table.plain_column_labels == ["other", "second_only"]
        command = next(
            command
            for command in app.get_system_commands(app.screen)
            if command.title == "Jump to Column"
        )
        await command.callback()
        await wait_for(
            pilot,
            lambda: (
                isinstance(app.screen, CommandPalette)
                and bool(app.screen.query(CommandList))
            ),
            description="the column picker",
        )
        command_list = app.screen.query_one(CommandList)
        await wait_for(
            pilot,
            lambda: command_list.option_count == 2,
            description="the active result's columns",
        )
        await pilot.press(*"second")
        await wait_for(
            pilot,
            lambda: command_list.option_count == 1,
            description="the second result's column",
        )
        await pilot.press("enter")
        await wait_for(pilot, lambda: table.has_focus, description="results focus")
        assert table.cursor_coordinate == Coordinate(0, 1)

        await pilot.press("ctrl+g")
        command_list = app.screen.query_one(CommandList)
        await pilot.press(*"other")
        await wait_for(
            pilot,
            lambda: command_list.option_count == 1,
            description="a different column in the original result",
        )
        app.results_viewer.action_switch_tab(-1)
        active_table = await wait_for_any_table(pilot, app)
        assert active_table is not table
        assert table.is_mounted
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, CommandPalette),
            description="the stale column picker to close",
        )
        assert app.results_viewer.get_visible_table() is active_table
        assert table.cursor_coordinate == Coordinate(0, 1)
        assert active_table.cursor_coordinate == Coordinate(0, 0)
        assert not table.has_focus
        await wait_for(
            pilot,
            lambda: active_table.has_focus,
            description="the active result's focus",
        )


@pytest.mark.asyncio
async def test_empty_result_has_no_column_jump(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        editor.text = "select 1 as value where 1 = 0"
        await pilot.press("ctrl+j")
        table = await wait_for_any_table(pilot, app)
        await wait_for(pilot, lambda: table.has_focus, description="results focus")
        await pilot.press("ctrl+g")
        assert not isinstance(app.screen, CommandPalette)
        assert "Jump to Column" not in {
            command.title for command in app.get_system_commands(app.screen)
        }
