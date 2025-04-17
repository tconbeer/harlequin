import sys
from pathlib import Path
from typing import Awaitable, Callable, List

import pytest

from harlequin import Harlequin
from harlequin.components import ExportScreen


def transaction_button_visible(app: Harlequin) -> bool:
    """
    Skip snapshot checks for versions of that app showing the autocommit button.
    """
    return sys.version_info >= (3, 12) and "Sqlite" in app.adapter.__class__.__name__


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    [
        "one.csv",
        "one.parquet",
        "one.json",
        pytest.param(
            "one.orc",
            marks=pytest.mark.skipif(
                sys.platform == "win32", reason="ORC not supported on Windows"
            ),
        ),
        "one.feather",
    ],
)
async def test_export(
    app_all_adapters: Harlequin,
    tmp_path: Path,
    filename: str,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = app_all_adapters
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select 1 as a, 2 as b"
        await pilot.press("ctrl+j")  # run query
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        assert len(app.screen_stack) == 1

        await pilot.press("ctrl+e")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert app.screen.id == "export_screen"
        assert isinstance(app.screen, ExportScreen)
        snap_results.append(await app_snapshot(app, "Export Screen"))

        app.screen.file_input.value = f"/tmp/foo-bar-static/{filename}"  # type: ignore
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Export with Path"))
        export_path = tmp_path / filename
        app.screen.file_input.value = str(export_path)  # type: ignore
        await pilot.pause()
        await pilot.press("enter")
        await wait_for_workers(app)
        await pilot.pause()

        # test the written file
        assert export_path.is_file()
        if export_path.suffix == ".json":
            with export_path.open("r") as f:
                line = f.readline()
                assert line == '{"a":1,"b":2}\n'

        # ensure we return to the main screen after export
        assert len(app.screen_stack) == 1
        await wait_for_workers(app)
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "After Export"))

        if not transaction_button_visible(app):
            assert all(snap_results)
