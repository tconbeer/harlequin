from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Awaitable, Callable, Type
from unittest.mock import MagicMock

import pytest
from textual.command import CommandList, CommandPalette
from textual.pilot import Pilot
from textual.widgets import Button, Tab, Tabs

from harlequin import Harlequin
from harlequin.catalog_cache import CatalogCache
from harlequin.components import HelpScreen
from harlequin.components.catalog_source_screen import CatalogSourceScreen
from harlequin.components.confirm_modal import ConfirmModal
from harlequin.components.data_catalog import S3Tree
from harlequin_duckdb.adapter import DuckDbAdapter
from tests.functional_tests.helpers import wait_for_editor, wait_for_error_modal
from tests.waiting import wait_for, wait_for_value


def palette_titles(app: Harlequin) -> set[str]:
    return {command.title for command in app.get_system_commands(app.screen)}


def highlighted_command(app: Harlequin) -> str:
    command_list = app.screen.query_one(CommandList)
    if command_list.highlighted is None:
        return ""
    option = command_list.get_option_at_index(command_list.highlighted)
    return str(getattr(option.prompt, "plain", option.prompt))


async def open_palette(pilot: Pilot, app: Harlequin, query: str) -> None:
    await pilot.press("ctrl+p")
    await wait_for(
        pilot,
        lambda: isinstance(app.screen, CommandPalette),
        description="the command palette to open",
    )
    await pilot.press(*query)

    await wait_for(
        pilot,
        lambda: query.lower() in highlighted_command(app).lower(),
        description=f"the palette to highlight {query!r}",
    )


async def run_palette_command(pilot: Pilot, app: Harlequin, title: str) -> None:
    await open_palette(pilot, app, title)
    await pilot.press("enter")
    await wait_for(
        pilot,
        lambda: not isinstance(app.screen, CommandPalette),
        description="the command palette to close",
    )


async def click_confirm_modal(pilot: Pilot, app: Harlequin, button_id: str) -> None:
    await wait_for(
        pilot,
        lambda: (
            isinstance(app.screen, ConfirmModal)
            and bool(app.screen.query(f"#{button_id}"))
        ),
        description="a prompt before discarding text",
    )
    await pilot.click(f"#{button_id}")


@pytest.mark.asyncio
async def test_palette_commands(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)

        commands = {
            command.title: command.help
            for command in app.get_system_commands(app.screen)
        }
        assert "Maximize" not in commands
        assert "Next Buffer" not in commands
        assert "Commit Transaction" not in commands
        assert commands["Export Data"].endswith("(^e)")
        assert commands["Run Query"].endswith("(^⏎ or ^j)")
        assert "Close All Buffers" in commands

        # the Query Editor is disabled while the Results Viewer is full screen
        app.results_viewer.focus()
        await wait_for(
            pilot,
            lambda: app.results_viewer.has_focus_within,
            description="the Results Viewer to take focus",
        )
        app.full_screen = True
        await wait_for(
            pilot,
            lambda: "Go To Line" not in palette_titles(app),
            description="the Query Editor's commands to be hidden",
        )
        assert "Toggle Full Screen" in palette_titles(app)
        app.full_screen = False

        await pilot.press("f1")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, HelpScreen),
            description="the help screen to open",
        )
        assert palette_titles(app) == {"Keys", "Change Theme", "Quit", "Screenshot"}


@pytest.mark.asyncio
async def test_palette_snapshots(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    snap_results: list[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)

        await open_palette(pilot, app, "buf")
        snap_results.append(await app_snapshot(app, "Search"))
        # the first escape hides the results, the second closes the palette
        await pilot.press("escape", "escape")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, CommandPalette),
            description="the command palette to close",
        )

        # the theme picker is a command palette too
        await open_palette(pilot, app, "Change Theme")
        command_palette = app.screen
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: (
                isinstance(app.screen, CommandPalette)
                and app.screen is not command_palette
            ),
            description="the theme picker to open",
        )
        await pilot.press(*"nord")
        await wait_for(
            pilot,
            lambda: highlighted_command(app) == "nord",
            description="the theme picker to highlight nord",
        )
        snap_results.append(await app_snapshot(app, "Theme Picker"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_palette_runs_buffer_commands(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection

        await run_palette_command(pilot, app, "New Buffer")
        await wait_for(
            pilot,
            lambda: collection.tab_count == 2,
            description="a second buffer to open",
        )

        # nothing to lose, so nothing to confirm
        await run_palette_command(pilot, app, "Close All Buffers")
        await wait_for(
            pilot,
            lambda: collection.tab_count == 1,
            description="the buffers to close without a prompt",
        )

        editor.text = "select 1"
        await run_palette_command(pilot, app, "New Buffer")
        await wait_for(
            pilot,
            lambda: collection.tab_count == 2,
            description="a second buffer to open",
        )

        await run_palette_command(pilot, app, "Close All Buffers")
        await wait_for(
            pilot,
            lambda: (
                isinstance(app.screen, ConfirmModal)
                and len(app.screen.query(Button)) == 2
            ),
            description="a prompt before discarding text",
        )
        assert [str(button.label) for button in app.screen.query(Button)] == [
            "Cancel",
            "Close All",
        ]
        await click_confirm_modal(pilot, app, "no")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, ConfirmModal),
            description="the prompt to close",
        )
        assert collection.tab_count == 2
        assert [buffer.text for buffer in collection.buffers] == ["select 1", ""]

        await run_palette_command(pilot, app, "Close All Buffers")
        await click_confirm_modal(pilot, app, "yes")
        await wait_for(
            pilot,
            lambda: collection.tab_count == 1 and editor.text == "",
            description="every buffer to close, leaving one empty one",
        )
        assert collection.has_class("hide-tabs")


@pytest.mark.asyncio
async def test_close_all_buffers_twice_at_once(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)
        collection = app.editor_collection
        for _ in range(3):
            await collection.action_new_buffer()
        assert collection.tab_count == 4

        await asyncio.gather(
            collection.action_close_all_buffers(),
            collection.action_close_all_buffers(),
        )
        assert collection.tab_count == 1
        assert app._exception is None


@pytest.mark.py12
@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="SQLite in Python < 3.12 has no transaction modes",
)
@pytest.mark.asyncio
async def test_palette_transaction_commands(
    app_small_sqlite: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = app_small_sqlite
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)
        await wait_for(
            pilot,
            lambda: "Toggle Transaction Mode" in palette_titles(app),
            description="the palette to offer the transaction mode",
        )
        assert "Commit Transaction" not in palette_titles(app)

        await run_palette_command(pilot, app, "Toggle Transaction Mode")
        await wait_for(
            pilot,
            lambda: (
                {"Commit Transaction", "Rollback Transaction"} <= palette_titles(app)
            ),
            description="the palette to offer commit and rollback",
        )
        connection = await wait_for_value(
            pilot, lambda: app.connection, description="the app to connect"
        )
        assert connection.transaction_mode is not None
        assert connection.transaction_mode.label == "Manual"


def catalog_tab_labels(app: Harlequin) -> list[str]:
    return [str(tab.label) for tab in app.data_catalog.query_one(Tabs).query(Tab)]


def file_tree_names(app: Harlequin) -> set[str]:
    file_tree = app.data_catalog.file_tree
    if file_tree is None:
        return set()
    return {str(node.label) for node in file_tree.root.children}


def active_catalog_tab(app: Harlequin) -> str:
    active_tab = app.data_catalog.query_one(Tabs).active_tab
    return "" if active_tab is None else str(active_tab.label)


async def press_next_catalog_tab(pilot: Pilot, app: Harlequin, expected: str) -> None:
    await wait_for(
        pilot,
        lambda: app.data_catalog.has_focus_within,
        description="the Data Catalog to have focus",
    )
    await pilot.press("k")
    await wait_for(
        pilot,
        lambda: active_catalog_tab(app) == expected,
        description=f"k to switch to the {expected} tab",
    )


async def submit_location(
    pilot: Pilot, app: Harlequin, location: str
) -> CatalogSourceScreen:
    source_screen = await wait_for_value(
        pilot,
        lambda: app.screen if isinstance(app.screen, CatalogSourceScreen) else None,
        description="the location prompt to open",
    )
    source_screen.location_input.value = location
    await pilot.press("enter")
    return source_screen


@pytest.mark.asyncio
async def test_show_files_command(
    duckdb_adapter: Type[DuckDbAdapter],
    data_dir: Path,
    tmp_path: Path,
    app_snapshot: Callable[..., Awaitable[bool]],
) -> None:
    files_dir = (data_dir / "functional_tests" / "files").relative_to(Path.cwd())
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "other.csv").write_text("a\n1\n")
    snap_results: list[bool] = []
    app = Harlequin(duckdb_adapter((":memory:",)))
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_editor(pilot, app)
        catalog = app.data_catalog
        assert catalog.file_tree is None
        assert catalog.has_class("hide-tabs")

        await run_palette_command(pilot, app, "Show Files")
        source_screen = await submit_location(
            pilot, app, (files_dir / "bar.csv").as_posix()
        )
        await wait_for(
            pilot,
            lambda: "is not a directory" in str(source_screen.error_label.content),
            description="the prompt to refuse a file",
        )
        snap_results.append(await app_snapshot(app, "Show Files refuses a file"))
        await submit_location(pilot, app, str(tmp_path / "missing"))
        await wait_for(
            pilot,
            lambda: "does not exist" in str(source_screen.error_label.content),
            description="the prompt to refuse a missing directory",
        )

        await submit_location(pilot, app, str(files_dir))
        await wait_for(
            pilot,
            lambda: "bar.csv" in file_tree_names(app),
            description="the Files tab to list the directory",
        )
        assert not catalog.has_class("hide-tabs")
        assert catalog_tab_labels(app) == ["Databases", "Files"]
        file_tree = catalog.file_tree
        assert file_tree is not None
        assert catalog.active_pane is file_tree.parent
        await wait_for(
            pilot,
            lambda: app.focused is file_tree,
            description="the Files tab to take focus",
        )
        snap_results.append(await app_snapshot(app, "Files tab added"))

        # the prompt starts at the directory shown, ready to autocomplete in it
        await run_palette_command(pilot, app, "Show Files")
        source_screen = await submit_location(pilot, app, str(other_dir))
        assert source_screen.current_location == f"{files_dir}{os.sep}"
        await wait_for(
            pilot,
            lambda: file_tree_names(app) == {"other.csv"},
            description="the Files tab to list the other directory",
        )
        assert catalog.file_tree is file_tree
        assert app.show_files == other_dir

        await run_palette_command(pilot, app, "Show Files")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, CatalogSourceScreen),
            description="the location prompt to open",
        )
        await pilot.press("escape")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, CatalogSourceScreen),
            description="escape to close the prompt",
        )
        assert file_tree_names(app) == {"other.csv"}

        assert all(snap_results)


@pytest.mark.asyncio
async def test_show_s3_command(
    duckdb_adapter: Type[DuckDbAdapter],
    data_dir: Path,
    mock_boto3: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_key = (None, "other-bucket", "")
    cached_listing: dict[str, dict] = {"other-bucket": {"cached.csv": {}}}
    live_listing_released = threading.Event()
    other_bucket = MagicMock(name="other_bucket")
    other_bucket.name = "other-bucket"

    def list_other_bucket() -> list[SimpleNamespace]:
        live_listing_released.wait(timeout=30)
        return [SimpleNamespace(key="live.csv")]

    other_bucket.objects.all.side_effect = list_other_bucket
    mock_s3 = mock_boto3.resource.return_value
    my_bucket = mock_s3.Bucket.return_value
    mock_s3.Bucket.side_effect = lambda name: (
        other_bucket if name == "other-bucket" else my_bucket
    )

    cached_trees: list[tuple[tuple[str | None, ...], dict | None]] = []

    def record_cached_tree(*_: object, s3_tree: S3Tree | None, **__: object) -> None:
        if s3_tree is not None:
            cached_trees.append((s3_tree.cache_key, s3_tree.catalog_data))

    monkeypatch.setattr("harlequin.app.update_catalog_cache", record_cached_tree)
    monkeypatch.setattr(
        "harlequin.app.get_catalog_cache",
        lambda: CatalogCache(databases={}, s3={other_key: cached_listing}),
    )

    app = Harlequin(duckdb_adapter((":memory:",)), show_s3="my-bucket")
    try:
        async with app.run_test(size=(120, 36)) as pilot:
            await wait_for_editor(pilot, app)
            catalog = app.data_catalog
            first_tree = await wait_for_value(
                pilot,
                lambda: (
                    catalog.s3_tree
                    if catalog.s3_tree is not None and catalog.s3_tree.catalog_data
                    else None
                ),
                description="the S3 tab to list my-bucket",
            )

            await run_palette_command(pilot, app, "Show S3")
            source_screen = await submit_location(pilot, app, "ftp://other-bucket")
            assert source_screen.current_location == "my-bucket"
            await wait_for(
                pilot,
                lambda: "is not an S3 URI" in str(source_screen.error_label.content),
                description="the prompt to refuse a non-S3 URI",
            )

            await submit_location(pilot, app, "s3://other-bucket")
            # the cached listing shows while the live one loads
            await wait_for(
                pilot,
                lambda: (
                    catalog.s3_tree is not first_tree
                    and catalog.s3_tree is not None
                    and catalog.s3_tree.catalog_data == cached_listing
                ),
                description="the S3 tab to show the cached listing",
            )
            assert cached_trees == [((None, "my-bucket", ""), first_tree.catalog_data)]
            assert app.show_s3 == "s3://other-bucket"
            assert not first_tree.is_attached

            live_listing_released.set()
            await wait_for(
                pilot,
                lambda: (
                    catalog.s3_tree is not None
                    and catalog.s3_tree.catalog_data is not None
                    and "live.csv" in catalog.s3_tree.catalog_data["other-bucket"]
                ),
                description="the S3 tab to show the live listing",
            )

            # Files is added between Databases and S3, and the tabs cycle in
            # the order they show
            await run_palette_command(pilot, app, "Show Files")
            await submit_location(
                pilot, app, str(data_dir / "functional_tests" / "files")
            )
            await wait_for(
                pilot,
                lambda: catalog_tab_labels(app) == ["Databases", "Files", "S3"],
                description="the Files tab to be added before S3",
            )
            for expected_tab in ["S3", "Databases", "Files"]:
                await press_next_catalog_tab(pilot, app, expected_tab)
    finally:
        live_listing_released.set()


@pytest.mark.asyncio
async def test_show_s3_without_boto3(
    app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("harlequin.components.data_catalog.boto3", None)
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_editor(pilot, app)
        assert "Show S3" not in palette_titles(app)
        assert "Show Files" in palette_titles(app)

        # bound to a key, it says what is missing
        app.action_show_s3()
        error_modal = await wait_for_error_modal(pilot, app)
        assert "boto3" in error_modal.text
