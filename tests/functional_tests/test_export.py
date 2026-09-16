import os
import sys
from pathlib import Path
from typing import Awaitable, Callable, List

import pytest
from textual.pilot import Pilot

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.components import ExportScreen
from tests.functional_tests.helpers import wait_for_any_table, wait_for_editor
from tests.waiting import wait_for, wait_for_value


async def open_export_screen(pilot: Pilot, app: Harlequin) -> ExportScreen:
    """Press the key that opens the Data Exporter, and return the screen."""
    await pilot.press("ctrl+e")
    return await wait_for_value(
        pilot,
        lambda: app.screen if isinstance(app.screen, ExportScreen) else None,
        description="the Export screen",
    )


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
    transaction_button_visible: Callable[[Harlequin], bool],
) -> None:
    app = app_all_adapters
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        editor.text = "select 1 as a, 2 as b"
        await pilot.press("ctrl+j")  # run query
        await wait_for_any_table(pilot, app)
        assert len(app.screen_stack) == 1

        export_screen = await open_export_screen(pilot, app)
        assert len(app.screen_stack) == 2
        assert export_screen.id == "export_screen"
        snap_results.append(await app_snapshot(app, "Export Screen"))

        export_screen.file_input.value = f"/tmp/foo-bar-static/{filename}"
        await pilot.pause()
        snap_results.append(await app_snapshot(app, "Export with Path"))
        export_path = tmp_path / filename
        export_screen.file_input.value = str(export_path)
        await pilot.pause()
        await pilot.press("enter")
        # the export runs on a worker, and the screen it was on comes back
        await wait_for(
            pilot,
            lambda: len(app.screen_stack) == 1 and export_path.is_file(),
            description="the export to be written and the Export screen to close",
        )
        if export_path.suffix == ".json":
            with export_path.open("r") as f:
                line = f.readline()
                assert line == '{"a":1,"b":2}\n'

        snap_results.append(await app_snapshot(app, "After Export"))

        if not transaction_button_visible(app):
            assert all(snap_results)


@pytest.mark.asyncio
async def test_export_result_with_no_rows(
    app_all_adapters: Harlequin,
    tmp_path: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A query that matched nothing exports a header and no rows.

    Not an error: an empty file is a true account of what the query returned,
    and it is what tells a reader "nothing matched" apart from "it failed".
    SQLite is the case that matters -- its cursor returns no backend at all
    for zero rows, so the columns come from the labels on screen.
    """
    app = app_all_adapters
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        editor.text = "select 1 as a, 2 as b where false"
        await pilot.press("ctrl+j")
        await wait_for_any_table(pilot, app)

        export_screen = await open_export_screen(pilot, app)

        export_path = tmp_path / "empty.csv"
        export_screen.file_input.value = str(export_path)
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(
            pilot, export_path.is_file, description="the export to be written"
        )

        assert export_path.read_text() == "a,b\n"
        # back on the main screen, i.e. no error modal
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_export_under_a_limit_stops_at_the_limit(
    app: Harlequin,
    tmp_path: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The fetch asks for one row more than the limit, to learn there are more.

    That row proves the truncation and is not part of what was asked for, so a
    limit of 5 exports five rows.
    """
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        app.run_query_bar.limit_input.value = "5"
        editor.text = "select * from range(100)"
        await pilot.press("ctrl+j")
        table = await wait_for_any_table(pilot, app)
        assert table.fetch_truncated is True

        export_screen = await open_export_screen(pilot, app)
        export_path = tmp_path / "limited.csv"
        export_screen.file_input.value = str(export_path)
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(
            pilot, export_path.is_file, description="the export to be written"
        )

        assert len(export_path.read_text().splitlines()) == 6  # header and 5 rows


@pytest.mark.asyncio
async def test_export_starts_at_the_export_path(
    duckdb_adapter: type[HarlequinAdapter],
    tmp_path: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """`-o` names the folder the Data Exporter opens in, so a user who exports
    into the same place every time types a file name and nothing else."""
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        export_path=str(tmp_path / "exports"),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        editor.text = "select 1 as a"
        await pilot.press("ctrl+j")
        await wait_for_any_table(pilot, app)

        export_screen = await open_export_screen(pilot, app)
        assert export_screen.file_input.value == f"{tmp_path / 'exports'}{os.sep}"

        # the folder does not exist yet, and exporting into it makes it
        export_screen.file_input.value += "one.csv"
        await pilot.pause()
        await pilot.press("enter")
        exported = tmp_path / "exports" / "one.csv"
        await wait_for(pilot, exported.is_file, description="the export to be written")

        assert exported.read_text() == "a\n1\n"
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_an_export_path_with_a_file_name_picks_its_format(
    duckdb_adapter: type[HarlequinAdapter],
    tmp_path: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A whole path prefills whole, and the extension chooses the format the
    same way a typed one does."""
    export_path = tmp_path / "out.parquet"
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        export_path=str(export_path),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        editor.text = "select 1 as a"
        await pilot.press("ctrl+j")
        await wait_for_any_table(pilot, app)

        export_screen = await open_export_screen(pilot, app)
        assert export_screen.file_input.value == str(export_path)
        assert export_screen.format_select.value == "parquet"
