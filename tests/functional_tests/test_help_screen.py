from typing import Awaitable, Callable

import pytest

from harlequin import Harlequin


@pytest.mark.asyncio
async def test_help_screen(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        assert len(app.screen_stack) == 1

        await pilot.press("f1")
        assert len(app.screen_stack) == 2
        assert app.screen.id == "help_screen"
        assert await app_snapshot(app, "Help Screen")

        await pilot.press("a")  # any key
        assert len(app.screen_stack) == 1

        app.results_viewer.focus()

        await pilot.press("f1")
        assert len(app.screen_stack) == 2

        await pilot.press("space")  # any key
        assert len(app.screen_stack) == 1
