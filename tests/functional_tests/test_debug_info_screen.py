from pathlib import Path
from typing import Awaitable, Callable

import pytest

from harlequin import Harlequin


@pytest.mark.asyncio
async def test_debug_info_screen(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path("tests/data/unit_tests/config/good_config.toml").resolve()
    monkeypatch.setattr(
        "harlequin.app.get_highest_priority_existing_config_file", lambda: config_path
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        assert len(app.screen_stack) == 1

        app.profile_name = "my-duckdb-profile"
        app.action_show_debug_info()
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert app.screen.id == "debug_info_screen"
        assert await app_snapshot(app, "Debug Info Screen")

        await pilot.press("tab")
        await pilot.press("tab")
        await pilot.press("shift+tab")
        await pilot.press("enter")
        await pilot.press("enter")
        await pilot.press("pageup")
        await pilot.press("pagedown")
        await pilot.press("esc")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        app.action_show_debug_info()
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await pilot.click()
        await pilot.pause()
        assert len(app.screen_stack) == 1

        app.action_show_debug_info()
        await pilot.pause()
        assert app.screen.id == "debug_info_screen"
        assert await app_snapshot(app, "Debug Info Screen Focus")
