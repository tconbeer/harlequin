from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from harlequin.components.debug_info import (
    AdapterDebugInfo,
    DebugInfoScreen,
    HarlequinDebugInfo,
)
from harlequin.config import Config


class _DebugHostApp(App[None]):
    def compose(self) -> ComposeResult:
        yield Label("host")


@pytest.mark.asyncio
async def test_debug_info_screen_move_focus_and_page_scroll() -> None:
    hq_info = HarlequinDebugInfo(
        all_keymaps=["vscode"],
        config=Config(),
        config_path=None,
        keymap_names=["vscode"],
        theme="harlequin",
        active_profile_name="default",
        active_profile_config={},
    ).parse_info()
    adapter_info = AdapterDebugInfo(
        adapter_options=[],
        adapter_type="duckdb",
        adapter_details="DuckDB adapter",
        adapter_driver_details="duckdb 1.0",
    ).parse_info()

    app = _DebugHostApp()
    async with app.run_test() as pilot:
        screen = DebugInfoScreen(
            harlequin_details=hq_info,
            adapter_details=adapter_info,
            id="debug_info_screen",
        )
        await app.push_screen(screen)
        await pilot.pause()

        expected_ids = [
            "collapsible-harlequin-details",
            "collapsible-active-profile-config",
            "collapsible-full-config",
            "collapsible-adapter-details",
            "collapsible-adapter-details-section",
            "collapsible-adapter-options",
        ]
        assert screen.focused is not None
        assert screen.focused.id == expected_ids[0]

        for expected_id in expected_ids[1:] + [expected_ids[0]]:
            screen.move_focus(1)
            await pilot.pause()
            assert screen.focused is not None
            assert screen.focused.id == expected_id

        scroll_container = screen.query_one("#debug_info_scroll")
        up_spy = MagicMock(wraps=scroll_container.scroll_page_up)
        down_spy = MagicMock(wraps=scroll_container.scroll_page_down)
        scroll_container.scroll_page_up = up_spy  # type: ignore[method-assign]
        scroll_container.scroll_page_down = down_spy  # type: ignore[method-assign]

        await pilot.press("pagedown")
        await pilot.pause()
        assert down_spy.call_count == 1

        await pilot.press("pageup")
        await pilot.pause()
        assert up_spy.call_count == 1

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
