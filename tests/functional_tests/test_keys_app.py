from __future__ import annotations

import sys
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from harlequin import HarlequinKeys
from textual.widgets import Input

USER_CONFIG_PATH = Path("/tmp") / "harlequin"


@pytest.fixture(autouse=True)
def mock_config_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "harlequin.keys_app.get_config_for_profile", lambda **_: (dict(), [])
    )
    monkeypatch.setattr(
        "harlequin.keys_app.get_highest_priority_existing_config_file", lambda **_: None
    )
    monkeypatch.setattr(
        "harlequin.keys_app.user_config_path", lambda **_: USER_CONFIG_PATH
    )


@pytest.fixture
def keys_app() -> HarlequinKeys:
    return HarlequinKeys()


@pytest.mark.asyncio
async def test_keys_app(
    keys_app: HarlequinKeys,
    app_snapshot: Callable[..., Awaitable[bool]],
) -> None:
    target_path = USER_CONFIG_PATH / "config.toml"
    target_path.unlink(missing_ok=True)
    app = keys_app
    snap_results: list[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        while (
            app.active_keymap_names is None
            or app.bindings is None
            or app.unmodifed_bindings is None
            or app.table is None
        ):
            await pilot.pause()
        snap_results.append(await app_snapshot(app, "Initialization"))

        await pilot.press("down", "down", "down", "down", "enter")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Edit Modal"))

        await pilot.press("enter")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Enter Key Modal"))

        await pilot.press("f3")
        await pilot.pause()
        await pilot.wait_for_animation()
        snap_results.append(await app_snapshot(app, "Edit Modal: f3"))

        await pilot.press("tab", "tab", "enter", "f4")
        await pilot.pause()
        await pilot.wait_for_animation()
        snap_results.append(await app_snapshot(app, "Edit Modal: f3 and f4"))

        await pilot.press("shift+tab", "enter")
        await pilot.pause()
        await pilot.wait_for_animation()
        snap_results.append(await app_snapshot(app, "Edit Modal: f4 removed"))

        await pilot.press("tab", "tab", "tab", "tab", "enter")
        await pilot.pause()
        await pilot.wait_for_animation()
        snap_results.append(await app_snapshot(app, "Main modal, Focus F3"))

        await pilot.press("ctrl+q")
        await pilot.pause()
        # the quit modal should now be visible. We make some tweaks so tests
        # pass consistently
        input_widgets = app.query(Input)
        for widget in input_widgets:
            widget.cursor_blink = False  # prevent flaky tests
        path_input = app.query_one("#path_input", expect_type=Input)
        assert path_input.value == str(target_path)
        if sys.platform == "win32":
            # on windows the path will use backslashes, which makes
            # the snapshot fail. Use forward slashes instead.
            path_input.value = target_path.as_posix()
        await pilot.wait_for_animation()
        snap_results.append(await app_snapshot(app, "Quit modal"))

        await pilot.press("f", "o", "o", "tab", "tab", "tab", "enter")
        assert app.return_code == 0
        assert target_path.exists()

        assert all(snap_results)
