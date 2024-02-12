import sys
from pathlib import Path
from typing import Awaitable, Callable, List

import pytest
from harlequin import Harlequin
from harlequin.components import ExportScreen


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
    app: Harlequin,
    tmp_path: Path,
    filename: str,
    app_snapshot: Callable[..., Awaitable[bool]],
) -> None:
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select 1 as a"
        await pilot.press("ctrl+j")  # run query
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
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
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert export_path.is_file()
        assert len(app.screen_stack) == 1
        await app.workers.wait_for_complete()
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "After Export"))

        assert all(snap_results)
