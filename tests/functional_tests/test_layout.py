from typing import Awaitable, Callable, List

import pytest
from harlequin import Harlequin


@pytest.mark.asyncio
async def test_toggle_sidebar(
    app: Harlequin, app_snapshot: Callable[..., Awaitable[bool]]
) -> None:
    snap_results: List[bool] = []
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None:
            await pilot.pause()
        # initialization
        sidebar = app.data_catalog
        assert not sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value > 0
        snap_results.append(await app_snapshot(app, "Initialization"))

        await pilot.press("ctrl+b")
        assert sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value == 0
        snap_results.append(await app_snapshot(app, "Hidden"))

        await pilot.press("ctrl+b")
        assert not sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value > 0
        snap_results.append(await app_snapshot(app, "Unhidden"))

        await pilot.press("f9")
        assert sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value == 0
        snap_results.append(await app_snapshot(app, "Hidden Again"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_toggle_full_screen(
    app: Harlequin, app_snapshot: Callable[..., Awaitable[bool]]
) -> None:
    snap_results: List[bool] = []
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None:
            await pilot.pause()
        # initialization; all visible
        app.editor.focus()
        assert app.full_screen is False
        assert app.sidebar_hidden is False
        widgets = [app.data_catalog, app.editor_collection, app.results_viewer]
        for w in widgets:
            assert not w.disabled
            assert w.styles.width
            assert w.styles.width.value > 0
        snap_results.append(await app_snapshot(app, "Initialization"))

        await pilot.press("f10")
        # only editor visible
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        assert not app.run_query_bar.disabled
        assert app.editor_collection.styles.width
        assert app.editor_collection.styles.width.value > 0
        for w in [w for w in widgets if w != app.editor_collection]:
            assert w.disabled
            assert w.styles.width
            assert w.styles.width.value == 0
        snap_results.append(await app_snapshot(app, "Editor Full Screen"))

        await pilot.press("ctrl+b")
        # editor and data catalog should be visible
        assert not app.sidebar_hidden
        assert not app.data_catalog.disabled
        assert app.full_screen
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        snap_results.append(await app_snapshot(app, "Editor Full Screen with Sidebar"))

        await pilot.press("f10")
        # all visible
        for w in widgets:
            assert not w.disabled
            assert w.styles.width
            assert w.styles.width.value > 0
        snap_results.append(
            await app_snapshot(app, "Exit Full Screen (sidebar already visible)")
        )

        await pilot.press("ctrl+b")
        # data catalog hidden
        assert app.sidebar_hidden
        assert app.data_catalog.disabled
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        snap_results.append(await app_snapshot(app, "Sidebar hidden"))

        await pilot.press("f10")
        # only editor visible
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        assert app.data_catalog.disabled
        assert app.results_viewer.disabled
        snap_results.append(
            await app_snapshot(app, "Editor Full Screen (sidebar already hidden)")
        )

        await pilot.press("f10")
        # data catalog should still be hidden
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        assert not app.run_query_bar.disabled
        assert app.data_catalog.disabled
        assert not app.results_viewer.disabled
        snap_results.append(
            await app_snapshot(app, "Exit Full Screen (sidebar remains hidden)")
        )
        app.editor.text = "select 1"
        await pilot.press("ctrl+j")

        app.results_viewer.focus()
        await pilot.press("f10")
        # only results viewer should be visible
        assert app.editor_collection.disabled
        assert app.run_query_bar.disabled
        assert app.data_catalog.disabled
        assert not app.results_viewer.disabled
        snap_results.append(await app_snapshot(app, "Results Viewer Full Screen"))

        await pilot.press("f9")
        # results viewer and data catalog should be visible
        assert not app.sidebar_hidden
        assert not app.data_catalog.disabled
        assert app.full_screen
        assert app.editor_collection.disabled
        assert app.run_query_bar.disabled
        assert not app.results_viewer.disabled
        snap_results.append(
            await app_snapshot(app, "Results Viewer Full Screen with Sidebar")
        )

        await pilot.press("f10")
        # all visible
        assert not app.sidebar_hidden
        assert not app.full_screen
        for w in widgets:
            assert not w.disabled
            assert w.styles.width
            assert w.styles.width.value > 0
        snap_results.append(
            await app_snapshot(app, "Exit RV Full Screen (sidebar visible)")
        )

        assert all(snap_results)
