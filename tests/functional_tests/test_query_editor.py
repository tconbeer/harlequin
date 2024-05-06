from __future__ import annotations

import sys
from typing import Awaitable, Callable, List

import pytest
from harlequin import Harlequin
from textual.widgets.text_area import Selection


def transaction_button_visible(app: Harlequin) -> bool:
    """
    Skip snapshot checks for versions of that app showing the autocommit button.
    """
    return sys.version_info >= (3, 12) and "Sqlite" in app.adapter.__class__.__name__


@pytest.mark.asyncio
async def test_query_formatting(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select\n\n1 FROM\n\n foo"

        await pilot.press("f4")
        assert app.editor.text == "select 1 from foo\n"


@pytest.mark.asyncio
async def test_multiple_buffers(
    app: Harlequin, app_snapshot: Callable[..., Awaitable[bool]]
) -> None:
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None:
            await pilot.pause()
        assert app.editor_collection
        assert app.editor_collection.tab_count == 1
        assert app.editor_collection.active == "tab-1"
        app.editor.text = "tab 1"
        await pilot.press("home")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Tab 1 of 1 (No tabs)"))

        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 2
        assert app.editor_collection.active == "tab-2"
        assert app.editor.text == ""
        app.editor.text = "tab 2"
        await pilot.press("home")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Tab 2 of 2"))

        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == ""
        app.editor.text = "tab 3"
        await pilot.press("home")
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Tab 3 of 3"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-1"
        assert app.editor.text == "tab 1"
        snap_results.append(await app_snapshot(app, "Tab 1 of 3"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-2"
        assert app.editor.text == "tab 2"
        snap_results.append(await app_snapshot(app, "Tab 2 of 3"))

        await pilot.press("ctrl+w")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 2
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == "tab 3"
        # TODO: bring back this flaky test.
        # snap_results.append(await app_snapshot(app, "Tab 3 after deleting 2"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.active == "tab-1"
        assert app.editor.text == "tab 1"
        snap_results.append(await app_snapshot(app, "Tab 1 of [1,3]"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == "tab 3"
        snap_results.append(await app_snapshot(app, "Tab 3 of [1,3]"))

        assert all(snap_results)


@pytest.mark.xfail(
    sys.platform in ("win32", "darwin"),
    reason="Scroll bar is a different size.",
)
@pytest.mark.asyncio
async def test_word_autocomplete(
    app_all_adapters: Harlequin, app_snapshot: Callable[..., Awaitable[bool]]
) -> None:
    app = app_all_adapters
    snap_results: List[bool] = []
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None or app.editor_collection.word_completer is None:
            await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "s"))

        await pilot.press("e")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "se"))

        await pilot.press("l")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "sel"))

        await pilot.press("backspace")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "se again"))

        await pilot.press("l")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "submitted"))

        if not (transaction_button_visible(app)):
            assert all(snap_results)


@pytest.mark.skipif(
    sys.platform == "win32", reason="Initial snapshot very flaky on windows."
)
@pytest.mark.asyncio
async def test_member_autocomplete(
    app_small_duck: Harlequin, app_snapshot: Callable[..., Awaitable[bool]]
) -> None:
    app = app_small_duck
    snap_results: List[bool] = []
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None or app.editor_collection.member_completer is None:
            await pilot.pause()
        app.editor.text = '"drivers"'
        app.editor.selection = Selection((0, 9), (0, 9))

        await pilot.press("full_stop")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "driver members"))

        await pilot.press("quotation_mark")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "with quote"))

        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "submitted"))

        assert all(snap_results)
