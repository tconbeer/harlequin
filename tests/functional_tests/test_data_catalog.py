import sys
from pathlib import Path
from typing import Awaitable, Callable, List, NamedTuple, Set, Type
from unittest.mock import MagicMock

import pytest
from textual.geometry import Offset
from textual.widgets import Input

from harlequin import Harlequin
from harlequin.catalog import InteractiveCatalogItem
from harlequin.components import ExportScreen
from harlequin_duckdb.adapter import DuckDbAdapter


class MockS3Object(NamedTuple):
    key: str


@pytest.fixture
def mock_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_boto3 = MagicMock(name="mock_boto3")
    mock_s3 = MagicMock(name="mock_s3")
    mock_boto3.resource.return_value = mock_s3
    mock_bucket = MagicMock(name="mock_bucket")
    mock_bucket.name = "my-bucket"
    mock_s3.Bucket.return_value = mock_bucket
    mock_s3.buckets.all.return_value = [mock_bucket]
    objects = [
        MockS3Object(key="one/alpha/foo.csv"),
        MockS3Object(key="one/bravo/bar.csv"),
        MockS3Object(key="two/apple/baz/qux.csv"),
    ]
    mock_bucket.objects.all.return_value = objects
    mock_bucket.objects.filter.return_value = objects

    monkeypatch.setattr("harlequin.components.data_catalog.boto3", mock_boto3)
    monkeypatch.setattr("harlequin.components.data_catalog.s3_tree.boto3", mock_boto3)


@pytest.mark.asyncio
async def test_data_catalog(
    app_multi_duck: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_pyperclip: MagicMock,
) -> None:
    snap_results: List[bool] = []
    app = app_multi_duck
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        catalog = app.data_catalog
        assert not catalog.database_tree.show_root

        # this test app has two databases attached.
        dbs = catalog.database_tree.root.children
        assert len(dbs) == 2

        # the first db is called "small"
        assert str(dbs[0].label) == "small db"
        assert dbs[0].data is not None
        assert dbs[0].data.qualified_identifier == '"small"'
        assert dbs[0].data.query_name == '"small"'
        assert dbs[0].is_expanded is False

        # the small db has two schemas, but you can't see them yet: a collapsed
        # node holds its children on `data` and only builds TreeNodes when it is
        # expanded, so that a huge catalog costs a screenful of nodes, not one
        # per object in the database.
        assert isinstance(dbs[0].data, InteractiveCatalogItem)
        while not dbs[0].data.loaded:
            await pilot.pause(0.1)
        assert len(dbs[0].data.children) == 2
        assert not dbs[0].children
        snap_results.append(await app_snapshot(app, "Initialization"))

        assert str(dbs[1].label) == "tiny db"
        assert dbs[0].is_expanded is False

        # click on "small" and see it expand.
        await pilot.click(catalog.__class__, offset=Offset(x=6, y=1))
        await pilot.pause()
        assert dbs[0].is_expanded is True
        assert dbs[1].is_expanded is False
        assert len(dbs[0].children) == 2
        assert all(not node.is_expanded for node in dbs[0].children)
        # the schemas are on screen now, so the catalog probes them; wait, or the
        # snapshot catches "empty" still showing an expand arrow it will lose.
        for schema in dbs[0].children:
            while not getattr(schema.data, "loaded", True):
                await pilot.pause()
        snap_results.append(await app_snapshot(app, "small expanded"))

        # small's second schema is "main". click "main"
        schema_main = dbs[0].children[1]
        await pilot.click(catalog.__class__, offset=Offset(x=8, y=3))
        await pilot.pause()
        assert schema_main.is_expanded is True
        assert catalog.database_tree.cursor_line == 2  # main is selected
        snap_results.append(await app_snapshot(app, "small.main expanded"))

        # ctrl+enter to insert into editor; editor gets focus
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert schema_main.is_expanded is True
        assert app.editor.text == '"small"."main"'
        assert not catalog.has_focus
        snap_results.append(await app_snapshot(app, "Inserted small.main"))

        # use keys to navigate the tree into main.drivers
        await pilot.press("f6")
        await pilot.pause()
        assert catalog.database_tree.has_focus
        await pilot.press("down")
        await pilot.press("space")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        col_node = catalog.database_tree.cursor_node
        assert col_node is not None
        assert col_node.data is not None
        assert col_node.data.qualified_identifier == '"small"."main"."drivers"."dob"'
        assert col_node.data.query_name == '"dob"'
        snap_results.append(await app_snapshot(app, "small.main.drivers.dob selected"))

        # copy it
        await pilot.press("ctrl+c")
        assert mock_pyperclip.paste() == '"dob"'

        # reset the editor, then insert "dob"
        app.editor.text = ""
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert app.editor.text == '"dob"'
        snap_results.append(await app_snapshot(app, "small.main.drivers.dob inserted"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_double_click_inserts_node_into_editor(
    app_multi_duck: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = app_multi_duck
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        catalog = app.data_catalog

        dbs = catalog.database_tree.root.children
        assert isinstance(dbs[0].data, InteractiveCatalogItem)
        while not dbs[0].data.loaded:
            await pilot.pause(0.1)

        # a single click on "small" selects and expands it, but inserts nothing
        await pilot.click(catalog.__class__, offset=Offset(x=6, y=1))
        await pilot.pause()
        assert app.editor.text == ""

        # a double click inserts the node's query name into the editor
        await pilot.double_click(catalog.__class__, offset=Offset(x=6, y=1))
        await pilot.pause()
        assert app.editor.text == '"small"'
        assert dbs[0].is_expanded is True


@pytest.mark.asyncio
async def test_file_tree(
    duckdb_adapter: Type[DuckDbAdapter],
    data_dir: Path,
    app_snapshot: Callable[..., Awaitable[bool]],
    mock_pyperclip: MagicMock,
) -> None:
    snap_results: List[bool] = []
    test_dir = data_dir / "functional_tests" / "files"
    relative_test_dir = test_dir.relative_to(Path.cwd())
    app = Harlequin(
        duckdb_adapter((":memory:",)),
        show_files=relative_test_dir,
    )
    async with app.run_test(size=(120, 36)) as pilot:
        while app.editor is None:
            await pilot.pause()
        catalog = app.data_catalog
        assert catalog.file_tree is not None

        await pilot.press("f6")  # focus catalog
        await pilot.press("k")  # show files
        snap_results.append(await app_snapshot(app, "Initialization"))

        await pilot.press("down")
        await pilot.press("enter")
        snap_results.append(await app_snapshot(app, "expanded foo dir"))

        await pilot.press("ctrl+c")
        assert mock_pyperclip.paste() == str(test_dir / "foo")

        assert all(snap_results)


@pytest.mark.py12
@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="3.12 renders CSS differently for this test..."
)
@pytest.mark.asyncio
async def test_s3_tree(
    duckdb_adapter: Type[DuckDbAdapter],
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_pyperclip: MagicMock,
    mock_boto3: None,
) -> None:
    snap_results: List[bool] = []
    app = Harlequin(
        duckdb_adapter((":memory:",)),
        show_s3="my-bucket",
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        catalog = app.data_catalog
        assert catalog.s3_tree is not None
        while not catalog.s3_tree.is_mounted:
            await pilot.pause()

        await pilot.press("f6")  # focus catalog
        await pilot.press("k")  # show s3
        snap_results.append(await app_snapshot(app, "Initialization"))

        await pilot.press("down")
        await pilot.press("enter")
        await pilot.press("down")
        await pilot.press("enter")
        snap_results.append(await app_snapshot(app, "expanded one dir"))

        await pilot.press("ctrl+c")
        assert mock_pyperclip.paste() == "s3://my-bucket/one"

        assert all(snap_results)


@pytest.mark.skipif("boto3" in sys.modules, reason="boto3 is installed.")
@pytest.mark.asyncio
async def test_s3_tree_does_not_crash_without_boto3(
    duckdb_adapter: Type[DuckDbAdapter],
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = Harlequin(
        duckdb_adapter((":memory:",)),
        show_s3="my-bucket",
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        assert await app_snapshot(app, "Error visible")


@pytest.mark.asyncio
async def test_context_menu(
    app_small_duck: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    expand_catalog_node: Callable[..., Awaitable[None]],
) -> None:
    app = app_small_duck
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        # we need to expand the data catalog to load items into the completer
        while (
            app.data_catalog.database_tree.loading
            or not app.data_catalog.database_tree.root.children
        ):
            await pilot.pause()
        for db_node in app.data_catalog.database_tree.root.children:
            await expand_catalog_node(pilot, db_node)
            for schema_node in db_node.children:
                schema_node.expand()

        app.data_catalog.focus()
        await pilot.press("full_stop")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "db context menu expanded"))

        await pilot.press("enter")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "db name inserted"))

        app.data_catalog.focus()
        await pilot.press("down")
        await pilot.press("full_stop")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "schema context menu expanded"))

        await pilot.press("escape")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("full_stop")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(await app_snapshot(app, "table context menu expanded"))

        assert all(snap_results)


def _file_tree_paths(app: Harlequin) -> Set[Path]:
    """Return the paths of the top-level nodes in the file tree."""
    assert app.data_catalog.file_tree is not None
    return {
        node.data.path
        for node in app.data_catalog.file_tree.root.children
        if node.data is not None
    }


@pytest.mark.asyncio
async def test_file_tree_refreshes_after_editor_save(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """
    Regression test for https://github.com/tconbeer/harlequin/issues/871:
    the file tree should refresh after Harlequin saves a file from the editor,
    without the user having to manually refresh the catalog.
    """
    app = Harlequin(duckdb_adapter((":memory:",)), show_files=tmp_path)
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        assert app.data_catalog.file_tree is not None

        new_file = tmp_path / "saved_by_harlequin.sql"
        assert new_file not in _file_tree_paths(app)

        app.editor.focus()
        app.editor.text = "select 1"
        await pilot.press("ctrl+s")
        await pilot.pause()
        save_input = app.editor.query_one("#textarea__save_input", Input)
        save_input.value = str(new_file)
        await pilot.press("enter")

        for _ in range(100):
            await pilot.pause()
            if new_file in _file_tree_paths(app):
                break

        assert new_file.is_file()
        assert new_file in _file_tree_paths(app)


@pytest.mark.asyncio
async def test_file_tree_refreshes_after_export(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """
    Regression test for https://github.com/tconbeer/harlequin/issues/871:
    the file tree should refresh after Harlequin exports data to a file,
    without the user having to manually refresh the catalog.
    """
    app = Harlequin(duckdb_adapter((":memory:",)), show_files=tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        while app.editor is None:
            await pilot.pause()

        app.editor.text = "select 1 as a, 2 as b"
        await pilot.press("ctrl+j")  # run query
        for _ in range(100):
            await pilot.pause()
            if app.results_viewer.get_visible_table() is not None:
                break
        assert app.results_viewer.get_visible_table() is not None

        export_path = tmp_path / "exported.csv"
        assert export_path not in _file_tree_paths(app)

        await pilot.press("ctrl+e")
        while not isinstance(app.screen, ExportScreen):
            await pilot.pause()
        app.screen.file_input.value = str(export_path)
        await pilot.pause()
        await pilot.press("enter")

        for _ in range(100):
            await pilot.pause()
            if export_path in _file_tree_paths(app):
                break

        assert export_path.is_file()
        assert export_path in _file_tree_paths(app)
