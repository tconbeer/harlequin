from typing import Awaitable, Callable

import pytest
from textual.widgets import Static

from harlequin import Harlequin
from harlequin.components import HelpScreen
from tests.functional_tests.helpers import wait_for_editor


@pytest.mark.asyncio
async def test_help_screen(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)
        assert len(app.screen_stack) == 1

        await pilot.press("f1")
        assert len(app.screen_stack) == 2
        assert app.screen.id == "help_screen"
        keys_panel_tip = str(app.screen.query_one("#keys_panel_tip", Static).content)
        assert "Press [b $secondary]f7[/]" in keys_panel_tip
        assert await app_snapshot(app, "Help Screen")

        await pilot.press("a")  # any key
        assert len(app.screen_stack) == 1

        app.results_viewer.focus()

        await pilot.press("f1")
        assert len(app.screen_stack) == 2

        await pilot.press("space")  # any key
        assert len(app.screen_stack) == 1


def test_help_screen_keys_panel_tip_without_a_binding() -> None:
    keys_panel_tip = HelpScreen(keys_panel_key=None)._keys_panel_tip()
    assert "command palette ([b $secondary]ctrl+p[/])" in keys_panel_tip
