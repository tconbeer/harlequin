from __future__ import annotations

import asyncio
import sys
from typing import Awaitable, Callable

import pytest
from textual.command import CommandList, CommandPalette
from textual.pilot import Pilot

from harlequin import Harlequin
from harlequin.components import HelpScreen
from harlequin.components.confirm_modal import ConfirmModal
from tests.functional_tests.helpers import wait_for_editor
from tests.waiting import wait_for, wait_for_value


def palette_titles(app: Harlequin) -> set[str]:
    return {command.title for command in app.get_system_commands(app.screen)}


def highlighted_command(app: Harlequin) -> str:
    command_list = app.screen.query_one(CommandList)
    if command_list.highlighted is None:
        return ""
    option = command_list.get_option_at_index(command_list.highlighted)
    return str(getattr(option.prompt, "plain", option.prompt))


async def open_palette(pilot: Pilot, app: Harlequin, query: str) -> None:
    await pilot.press("ctrl+p")
    await wait_for(
        pilot,
        lambda: isinstance(app.screen, CommandPalette),
        description="the command palette to open",
    )
    await pilot.press(*query)

    await wait_for(
        pilot,
        lambda: query.lower() in highlighted_command(app).lower(),
        description=f"the palette to highlight {query!r}",
    )


async def run_palette_command(pilot: Pilot, app: Harlequin, title: str) -> None:
    await open_palette(pilot, app, title)
    await pilot.press("enter")
    await wait_for(
        pilot,
        lambda: not isinstance(app.screen, CommandPalette),
        description="the command palette to close",
    )


async def click_confirm_modal(pilot: Pilot, app: Harlequin, button_id: str) -> None:
    await wait_for(
        pilot,
        lambda: (
            isinstance(app.screen, ConfirmModal)
            and bool(app.screen.query(f"#{button_id}"))
        ),
        description="a prompt before discarding text",
    )
    await pilot.click(f"#{button_id}")


@pytest.mark.asyncio
async def test_palette_commands(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)

        commands = {
            command.title: command.help
            for command in app.get_system_commands(app.screen)
        }
        assert "Maximize" not in commands
        assert "Next Buffer" not in commands
        assert "Commit Transaction" not in commands
        assert commands["Export Data"].endswith("(^e)")
        assert commands["Run Query"].endswith("(^⏎ or ^j)")
        assert "Close All Buffers" in commands

        # the Query Editor is disabled while the Results Viewer is full screen
        app.results_viewer.focus()
        await wait_for(
            pilot,
            lambda: app.results_viewer.has_focus_within,
            description="the Results Viewer to take focus",
        )
        app.full_screen = True
        await wait_for(
            pilot,
            lambda: "Go To Line" not in palette_titles(app),
            description="the Query Editor's commands to be hidden",
        )
        assert "Toggle Full Screen" in palette_titles(app)
        app.full_screen = False

        await pilot.press("f1")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, HelpScreen),
            description="the help screen to open",
        )
        assert palette_titles(app) == {"Keys", "Change Theme", "Quit", "Screenshot"}


@pytest.mark.asyncio
async def test_palette_snapshots(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    snap_results: list[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)

        await open_palette(pilot, app, "buf")
        snap_results.append(await app_snapshot(app, "Search"))
        # the first escape hides the results, the second closes the palette
        await pilot.press("escape", "escape")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, CommandPalette),
            description="the command palette to close",
        )

        # the theme picker is a command palette too
        await open_palette(pilot, app, "Change Theme")
        command_palette = app.screen
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: (
                isinstance(app.screen, CommandPalette)
                and app.screen is not command_palette
            ),
            description="the theme picker to open",
        )
        await pilot.press(*"nord")
        await wait_for(
            pilot,
            lambda: highlighted_command(app) == "nord",
            description="the theme picker to highlight nord",
        )
        snap_results.append(await app_snapshot(app, "Theme Picker"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_palette_runs_buffer_commands(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection

        await run_palette_command(pilot, app, "New Buffer")
        await wait_for(
            pilot,
            lambda: collection.tab_count == 2,
            description="a second buffer to open",
        )

        # nothing to lose, so nothing to confirm
        await run_palette_command(pilot, app, "Close All Buffers")
        await wait_for(
            pilot,
            lambda: collection.tab_count == 1,
            description="the buffers to close without a prompt",
        )

        editor.text = "select 1"
        await run_palette_command(pilot, app, "New Buffer")
        await wait_for(
            pilot,
            lambda: collection.tab_count == 2,
            description="a second buffer to open",
        )

        await run_palette_command(pilot, app, "Close All Buffers")
        await click_confirm_modal(pilot, app, "no")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, ConfirmModal),
            description="the prompt to close",
        )
        assert collection.tab_count == 2
        assert [buffer.text for buffer in collection.buffers] == ["select 1", ""]

        await run_palette_command(pilot, app, "Close All Buffers")
        await click_confirm_modal(pilot, app, "yes")
        await wait_for(
            pilot,
            lambda: collection.tab_count == 1 and editor.text == "",
            description="every buffer to close, leaving one empty one",
        )
        assert collection.has_class("hide-tabs")


@pytest.mark.asyncio
async def test_close_all_buffers_twice_at_once(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)
        collection = app.editor_collection
        for _ in range(3):
            await collection.action_new_buffer()
        assert collection.tab_count == 4

        await asyncio.gather(
            collection.action_close_all_buffers(),
            collection.action_close_all_buffers(),
        )
        assert collection.tab_count == 1
        assert app._exception is None


@pytest.mark.py12
@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="SQLite in Python < 3.12 has no transaction modes",
)
@pytest.mark.asyncio
async def test_palette_transaction_commands(
    app_small_sqlite: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = app_small_sqlite
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)
        await wait_for(
            pilot,
            lambda: "Toggle Transaction Mode" in palette_titles(app),
            description="the palette to offer the transaction mode",
        )
        assert "Commit Transaction" not in palette_titles(app)

        await run_palette_command(pilot, app, "Toggle Transaction Mode")
        await wait_for(
            pilot,
            lambda: (
                {"Commit Transaction", "Rollback Transaction"} <= palette_titles(app)
            ),
            description="the palette to offer commit and rollback",
        )
        connection = await wait_for_value(
            pilot, lambda: app.connection, description="the app to connect"
        )
        assert connection.transaction_mode is not None
        assert connection.transaction_mode.label == "Manual"
