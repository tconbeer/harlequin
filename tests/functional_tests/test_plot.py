import sys
from typing import Awaitable, Callable, List

import pytest

from harlequin import Harlequin
from harlequin.components import PlotScreen
from harlequin.components.plot_screen import MAX_ELEM_DEFAULT, PlotType


def transaction_button_visible(app: Harlequin) -> bool:
    """
    Skip snapshot checks for versions of that app showing the autocommit button.
    """
    return sys.version_info >= (3, 12) and "Sqlite" in app.adapter.__class__.__name__


@pytest.mark.asyncio
async def test_plot(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    assert MAX_ELEM_DEFAULT > 0
    app = app_all_adapters
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select 1 as a, 3 as b"
        await pilot.press("ctrl+j")  # run query
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        assert len(app.screen_stack) == 1

        app.plots_metadata[0].max_elem = 10
        app.plots_metadata[0].x = "a"
        app.plots_metadata[0].y = "b"

        await pilot.press("ctrl+d")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert app.screen.id == "plot_screen"
        assert isinstance(app.screen, PlotScreen)
        snap_results.append(await app_snapshot(app, "Plot Screen"))

        # ensure we return to the main screen after export
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        await wait_for_workers(app)
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "After Plot Screen"))

        if not transaction_button_visible(app):
            assert all(snap_results)


@pytest.mark.asyncio
async def test_plot_2(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    assert MAX_ELEM_DEFAULT > 0
    app = app_all_adapters
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = """SELECT 1 AS a, 2 as b
UNION ALL
SELECT 4, 2
UNION ALL
SELECT 8, 2"""
        await pilot.press("ctrl+j")  # run query
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        assert len(app.screen_stack) == 1

        app.plots_metadata[0].max_elem = 10
        app.plots_metadata[0].type = PlotType.LINE
        app.plots_metadata[0].x = "a"
        app.plots_metadata[0].y = "b"

        await pilot.press("ctrl+d")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert app.screen.id == "plot_screen"
        assert isinstance(app.screen, PlotScreen)
        snap_results.append(await app_snapshot(app, "Plot Screen"))

        # ensure we return to the main screen after export
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        await wait_for_workers(app)
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "After Plot Screen"))

        if not transaction_button_visible(app):
            assert all(snap_results)
