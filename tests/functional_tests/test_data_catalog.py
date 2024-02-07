import sys
from pathlib import Path
from typing import Awaitable, Callable, List, NamedTuple, Type
from unittest.mock import MagicMock

import pytest
from harlequin import Harlequin
from harlequin_duckdb.adapter import DuckDbAdapter
from textual.geometry import Offset


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


@pytest.mark.asyncio
async def test_data_catalog(
    app_multi_duck: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    mock_pyperclip: MagicMock,
) -> None:
    snap_results: List[bool] = []
    app = app_multi_duck
    async with app.run_test(size=(120, 36)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        catalog = app.data_catalog
        assert not catalog.database_tree.show_root
        snap_results.append(await app_snapshot(app, "Initialization"))

        # this test app has two databases attached.
        dbs = catalog.database_tree.root.children
        assert len(dbs) == 2

        # the first db is called "small"
        assert str(dbs[0].label) == "small db"
        assert dbs[0].data is not None
        assert dbs[0].data.qualified_identifier == '"small"'
        assert dbs[0].data.query_name == '"small"'
        assert dbs[0].is_expanded is False

        # the small db has two schemas, but you can't see them yet
        assert len(dbs[0].children) == 2
        assert all(not node.is_expanded for node in dbs[0].children)

        assert str(dbs[1].label) == "tiny db"
        assert dbs[0].is_expanded is False

        # click on "small" and see it expand.
        await pilot.click(catalog.__class__, offset=Offset(x=6, y=1))
        assert dbs[0].is_expanded is True
        assert dbs[1].is_expanded is False
        assert all(not node.is_expanded for node in dbs[0].children)
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
async def test_file_tree(
    duckdb_adapter: Type[DuckDbAdapter],
    data_dir: Path,
    app_snapshot: Callable[..., Awaitable[bool]],
    mock_pyperclip: MagicMock,
) -> None:
    snap_results: List[bool] = []
    test_dir = (data_dir / "functional_tests" / "files").relative_to(Path.cwd())
    app = Harlequin(
        duckdb_adapter((":memory:",)),
        show_files=test_dir,
    )
    async with app.run_test(size=(120, 36)) as pilot:
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


@pytest.mark.asyncio
async def test_s3_tree(
    duckdb_adapter: Type[DuckDbAdapter],
    app_snapshot: Callable[..., Awaitable[bool]],
    mock_pyperclip: MagicMock,
    mock_boto3: None,
) -> None:
    snap_results: List[bool] = []
    app = Harlequin(
        duckdb_adapter((":memory:",)),
        show_s3="my-bucket",
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        catalog = app.data_catalog
        assert catalog.s3_tree is not None

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
) -> None:
    app = Harlequin(
        duckdb_adapter((":memory:",)),
        show_s3="my-bucket",
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert await app_snapshot(app, "Error visible")
