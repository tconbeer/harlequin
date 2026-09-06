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
        await pilot.press("escape")
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


def test_the_debug_screen_prints_no_secret() -> None:
    """The screen a user screenshots into an issue.

    Built directly rather than driven through the app: what is asserted is the
    content of the panels, and a snapshot of a screen would prove it for one
    theme and one terminal size.
    """
    from harlequin.components.debug_info import HarlequinDebugInfo
    from harlequin.config import Config
    from harlequin.options import TextOption

    secret = "hunter2-and-then-some"
    profile = {
        "adapter": "duckdb",
        "conn_str": [f"md:my_db?motherduck_token={secret}"],
        "md_token": secret,
        "theme": "fruity",
    }
    options = [TextOption(name="md_token", description="A token.", secret=True)]
    widgets = HarlequinDebugInfo(
        all_keymaps=["vscode"],
        config=Config(default_profile="md", profiles={"md": profile}),
        config_path=Path(".harlequin.toml"),
        active_profile_name="md",
        active_profile_config=profile,
        adapter_options=options,
    ).parse_info()

    printed = _every_string(widgets)
    assert secret not in printed
    assert "********" in printed
    # and still the screen it was: what a reader needs is all still there
    assert "fruity" in printed


def test_the_debug_screen_names_the_tunnel_without_its_credential() -> None:
    """`--ssh-host` takes an `ssh://user:pw@host`, and nothing else strips it."""
    from harlequin.components.debug_info import HarlequinDebugInfo
    from harlequin.config import Config

    widgets = HarlequinDebugInfo(
        all_keymaps=[],
        config=Config(),
        config_path=None,
        ssh_tunnel="localhost:15439 -> db:5439 via ssh://tco:hunter2@bastion",
    ).parse_info()

    printed = _every_string(widgets)
    assert "localhost:15439 -> db:5439" in printed
    assert "hunter2" not in printed


def test_the_debug_screen_prints_no_secret_default() -> None:
    """An adapter that ships a default for a secret has shipped the secret."""
    from harlequin.components.debug_info import AdapterDebugInfo
    from harlequin.options import TextOption

    secret = "hunter2-and-then-some"
    widgets = AdapterDebugInfo(
        adapter_options=[
            TextOption(name="md_token", description="x", default=secret, secret=True),
            TextOption(name="host", description="x", default="warehouse"),
        ],
        adapter_type="DuckDbAdapter",
        adapter_details=None,
        adapter_driver_details=None,
    ).parse_info()

    printed = _every_string(widgets)
    assert secret not in printed
    assert "********" in printed
    assert "warehouse" in printed


def _every_string(widgets: object) -> str:
    """Every piece of text a list of debug widgets would render, flattened."""
    from harlequin.components.debug_info import DebugWidget

    if isinstance(widgets, DebugWidget):
        return f"{widgets.title}\n{_every_string(widgets.content)}"
    if isinstance(widgets, list):
        return "\n".join(_every_string(item) for item in widgets)
    return str(widgets)
