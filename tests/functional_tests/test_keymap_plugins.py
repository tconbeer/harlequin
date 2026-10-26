"""Opt-in keymaps, loaded beside the author's VS Code map."""

from __future__ import annotations

from typing import Awaitable, Callable

import pytest

from harlequin.adapter import HarlequinAdapter
from harlequin.app import Harlequin
from harlequin.bindings import KEYMAP_BINDING_ID
from harlequin.components.connection_screens import OpenConnectionScreen
from tests.functional_tests.helpers import wait_for_editor


def _bound_keys(app: Harlequin, action: str) -> list[str]:
    return [
        binding.key
        for _, binding in app._bindings
        if binding.action == action and binding.id == KEYMAP_BINDING_ID
    ]


@pytest.mark.asyncio
async def test_the_files_keymap_reloads_without_stacking_bindings(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="files",
        keymap_names=("vscode", "files"),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        assert _bound_keys(app, "show_files") == ["ctrl+shift+e"]
        assert _bound_keys(app, "open_folder") == ["ctrl+shift+o"]
        tab_keys = [
            binding.key
            for _, binding in app.screen._bindings
            if binding.action == "focus_next" and binding.id == KEYMAP_BINDING_ID
        ]
        assert tab_keys == ["tab"]
        load_keys = [
            binding.key
            for _, binding in editor._bindings
            if binding.action == "load" and binding.id == KEYMAP_BINDING_ID
        ]
        assert load_keys == ["ctrl+o"]
        app.action_bind_keymaps("vscode", "files")
        app.action_bind_keymaps("vscode", "files")
        await pilot.pause()
        assert _bound_keys(app, "show_files") == ["ctrl+shift+e"]


@pytest.mark.asyncio
async def test_vim_normal_mode_moves_and_insert_mode_types(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="vim",
        keymap_names=("vim",),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        assert editor.text_input is not None
        editor.text = "select 1\nselect 2\n"
        editor.text_input.focus()
        await pilot.pause()
        await pilot.press("escape", "j")
        assert editor.selection.start == editor.selection.end == (1, 0)
        await pilot.press("i", "x")
        assert editor.text.splitlines()[1].startswith("x")


@pytest.mark.asyncio
async def test_a_second_connection_can_be_installed_and_left(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="shelf",
        adapter_name="duckdb",
        conn_str=(":memory:",),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        assert len(app.connections) == 1
        first = app.connection
        app.action_open_connection()
        await pilot.pause()
        assert isinstance(app.screen, OpenConnectionScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.connections) == 1

        extra_adapter = duckdb_adapter([":memory:"], no_init=True)
        extra = extra_adapter.connect()
        app._install_extra_connection(
            app._connection_request, "duckdb", extra_adapter, extra, (":memory:",)
        )
        await wait_for_workers(app)
        assert len(app.connections) == 2
        assert app.connection is extra
        app._switch_connection_at(0)
        await wait_for_workers(app)
        assert app.connection is first
        closed = app.connections.close_at(1)
        assert closed is not None
        closed.connection.close()
