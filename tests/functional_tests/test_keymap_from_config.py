from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Awaitable, Callable

import pytest
from textual import events

from harlequin import Harlequin, HarlequinAdapter
from harlequin.config import load_profile_and_keymaps

QUERY = dedent(
    """
    select *
    from
        (
            values
                (1, 2, 3),
                (4, 5, 6),
                (7, 8, 9),
                (10, 11, 12),
                (13, 14, 15),
                (16, 17, 18),
                (19, 20, 21)
        ) foo(a, b, c)
"""
).strip()


@pytest.mark.asyncio
async def test_results_viewer_bindings(
    duckdb_adapter: type[HarlequinAdapter],
    data_dir: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    config_path = (
        data_dir / "functional_tests" / "test_keymap_from_config" / "config.toml"
    )
    profile, my_keymaps = load_profile_and_keymaps(
        config_path=config_path, profile_name=None
    )
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        keymap_names=profile["keymap_name"],
        user_defined_keymaps=my_keymaps,
    )
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        q = QUERY
        app.editor.text = q
        await pilot.press("ctrl+j")

        while (table := app.results_viewer.get_visible_table()) is None:
            await pilot.pause()

        assert table is not None
        assert table.cursor_coordinate == (0, 0)
        assert table.selection_anchor_coordinate is None

        # simple navigation
        await pilot.press("s")
        assert table.cursor_coordinate == (1, 0)
        assert table.selection_anchor_coordinate is None
        await pilot.press("d")
        assert table.cursor_coordinate == (1, 1)
        assert table.selection_anchor_coordinate is None
        await pilot.press("d")
        assert table.cursor_coordinate == (1, 2)
        assert table.selection_anchor_coordinate is None
        await pilot.press("a")
        assert table.cursor_coordinate == (1, 1)
        assert table.selection_anchor_coordinate is None
        await pilot.press("w")
        assert table.cursor_coordinate == (0, 1)
        assert table.selection_anchor_coordinate is None


def press_alt(app: Harlequin, character: str) -> None:
    """Sends alt+<character> the way a terminal does.

    ESC + n parses to the key `alt+n` carrying the character "n", which
    `pilot.press()` does not send and a focused TextArea would insert.
    """
    key_event = events.Key(f"alt+{character}", character)
    key_event.set_sender(app)
    assert app._driver is not None
    app._driver.send_message(key_event)


@pytest.mark.asyncio
async def test_alt_letter_binding_beats_the_focused_editor(
    duckdb_adapter: type[HarlequinAdapter],
    data_dir: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """alt+n opens a buffer instead of typing an "n" into the focused one."""
    config_path = (
        data_dir / "functional_tests" / "test_keymap_from_config" / "config.toml"
    )
    profile, my_keymaps = load_profile_and_keymaps(
        config_path=config_path, profile_name="alt_keys"
    )
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        keymap_names=profile["keymap_name"],
        user_defined_keymaps=my_keymaps,
    )
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        app.editor.text = "select 1"
        app.editor.focus()
        await pilot.press("ctrl+end")
        assert app.editor_collection.tab_count == 1

        press_alt(app, "n")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()

        assert app.editor_collection.tab_count == 2
        assert app.editor.text == ""

        # the buffer the editor was on did not get an "n" typed into it
        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.active == "tab-1"
        assert app.editor.text == "select 1"
