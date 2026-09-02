import sys
import threading
from pathlib import Path
from typing import (
    Awaitable,
    Callable,
    ClassVar,
    List,
    NamedTuple,
    Set,
    Type,
    cast,
)
from unittest.mock import MagicMock

import duckdb
import pytest
from rich.style import Style
from rich.text import Text
from textual import work
from textual.geometry import Offset
from textual.widgets import Input, Tooltip
from textual.worker import WorkerState

from harlequin import Harlequin
from harlequin.autocomplete.completers import BUFFER_TYPE_LABEL
from harlequin.catalog import CatalogItem, InteractiveCatalogItem
from harlequin.components import ErrorModal, ExportScreen
from harlequin.components.data_catalog.database_tree import DatabaseTree
from harlequin_duckdb.adapter import DuckDbAdapter


class MockS3Object(NamedTuple):
    key: str


class SimpleCatalogItem(InteractiveCatalogItem):
    """An interactive item whose children fetch as an empty list."""


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


class BlockingCatalogItem(InteractiveCatalogItem):
    """A catalog item whose fetch_children() blocks until it is released."""

    started: ClassVar[threading.Event] = threading.Event()
    release: ClassVar[threading.Event] = threading.Event()

    def fetch_children(self) -> List[CatalogItem]:
        type(self).started.set()
        type(self).release.wait(timeout=10)
        return []


@pytest.mark.asyncio
async def test_reload_while_loader_is_fetching(
    duckdb_adapter: Type[DuckDbAdapter],
) -> None:
    """Reloading the catalog mid-fetch must not crash the background loader.

    reload() replaces the tree's load queue. The loader used to call task_done()
    on whatever `self._load_queue` pointed at when its fetch returned, so a reload
    that landed mid-fetch made it call task_done() on the fresh queue -- which has
    no outstanding get() -- and the worker died with "task_done() called too many
    times" ([#991](https://github.com/tconbeer/harlequin/issues/991)).
    """
    BlockingCatalogItem.started.clear()
    BlockingCatalogItem.release.clear()

    app = Harlequin(duckdb_adapter((":memory:",)))
    async with app.run_test(size=(120, 36)) as pilot:
        tree = app.data_catalog.database_tree
        while tree.loading:
            await pilot.pause()

        item = BlockingCatalogItem(
            qualified_identifier="blocking",
            query_name="blocking",
            label="blocking",
            type_label="t",
        )
        tree.root.add("blocking", data=item)
        tree._add_to_load_queue(item, priority=0)

        # wait until the adapter call is actually in flight
        while not BlockingCatalogItem.started.is_set():
            await pilot.pause()

        # ... and swap the queue out from under the in-flight loader
        await tree.reload()
        BlockingCatalogItem.release.set()
        for _ in range(50):
            await pilot.pause()

        loaders = [
            w for w in app.workers if w.name == "_database_tree_background_loader"
        ]
        assert not [w.error for w in loaders if w.error is not None]


@pytest.mark.asyncio
async def test_tooltip_shows_the_full_label_of_a_truncated_item(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    expand_catalog_node: Callable[..., Awaitable[None]],
) -> None:
    """A catalog item too wide for the catalog gets a tooltip on hover.

    ([#1104](https://github.com/tconbeer/harlequin/issues/1104))
    """
    db_path = tmp_path / "tooltip.db"
    conn = duckdb.connect(str(db_path))
    # the brackets are a type label's shape ("[s]" is a list of strings), so this
    # name also pins that a label never reaches the tooltip as console markup
    conn.execute('create table "a_table_[s]_with_a_name_too_long_to_fit" (a int)')
    conn.close()

    app = Harlequin(duckdb_adapter([str(db_path)], no_init=True), connection_hash="tt")
    async with app.run_test(size=(120, 36), tooltips=True) as pilot:
        await wait_for_workers(app)
        tree = app.data_catalog.database_tree
        while tree.loading or not tree.root.children:
            await pilot.pause()
        db_node = tree.root.children[0]
        await expand_catalog_node(pilot, db_node)
        await expand_catalog_node(pilot, db_node.children[0])
        await pilot.pause()

        # the db's own label fits in the catalog, so it gets no tooltip
        await pilot.hover(app.data_catalog.__class__, offset=Offset(x=6, y=1))
        await pilot.pause()
        assert tree.hover_line == 0
        assert tree.tooltip is None

        # the table's does not, so hovering it shows the label and type label
        await pilot.hover(app.data_catalog.__class__, offset=Offset(x=6, y=3))
        await pilot.pause()
        assert tree.hover_line == 2
        assert isinstance(tree.tooltip, Text)
        assert tree.tooltip.plain == "a_table_[s]_with_a_name_too_long_to_fit t"

        # and the type label keeps the muted color it has in the tree
        type_label_span = tree.tooltip.spans[-1]
        assert tree.tooltip.plain[type_label_span.start : type_label_span.end] == "t"
        assert isinstance(type_label_span.style, Style)
        assert type_label_span.style.color is not None

        tooltip = app.screen.get_child_by_type(Tooltip)
        await pilot.pause(app.TOOLTIP_DELAY + 0.1)
        assert tooltip.display


@pytest.mark.asyncio
async def test_buffer_symbols_load_the_items_they_name(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The catalog loads the children of the items the query editor names.

    Lazy loading otherwise leaves a schema's relations (and their columns) out of
    the completions until the user expands the schema in the Data Catalog
    ([#752](https://github.com/tconbeer/harlequin/issues/752)).
    """
    db_path = tmp_path / "symbols.db"
    conn = duckdb.connect(str(db_path))
    conn.execute("create schema my_schema")
    conn.execute("create table my_schema.my_table (my_col int)")
    conn.close()

    app = Harlequin(
        duckdb_adapter((str(db_path),), no_init=True),
        connection_hash="symbols",
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        tree = app.data_catalog.database_tree
        while (
            tree.loading
            or app.editor is None
            or app.editor_collection.member_completer is None
        ):
            await pilot.pause()
        member_completer = app.editor_collection.member_completer

        database_item = tree.root.data.children[0] if tree.root.data else None
        assert database_item is not None
        while not getattr(database_item, "loaded", False):
            await pilot.pause()
        schema_item = next(
            item for item in database_item.children if item.label == "my_schema"
        )
        assert not schema_item.children
        assert not member_completer("my_schema.my_t")

        app.editor.text = "select * from my_schema.my_table"

        # the editor re-reads the buffer on a timer, the tree loads the schema
        # the buffer names, and then the relation it names under it
        for _ in range(100):
            if member_completer("my_table.my_c"):
                break
            await pilot.pause(0.1)

        # the buffer names my_table, so its own completion for it is not proof;
        # the column is one only the catalog could have offered.
        (schema_label, schema_value), *_ = member_completer("my_schema.my_t")
        assert schema_value == "my_schema.my_table"
        assert schema_label[1] != BUFFER_TYPE_LABEL
        (column_label, column_value), *_ = member_completer("my_table.my_c")
        assert column_value == "my_table.my_col"
        assert column_label[1] != BUFFER_TYPE_LABEL


@pytest.mark.asyncio
async def test_child_worker_failure_is_surfaced_and_loader_continues(
    duckdb_adapter: Type[DuckDbAdapter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected failure in the loader's per-item worker shows one catalog
    modal, and the loader goes on to the next item.

    _load_children catches adapter failures itself; a child worker that fails
    anyway reaches the loader's WorkerFailed path, and its StateChanged ERROR
    is surfaced by DatabaseTree rather than crashing Harlequin (regression
    test for #1117).
    """
    poison_item = SimpleCatalogItem(
        qualified_identifier="poison",
        query_name="poison",
        label="poison",
        type_label="t",
    )
    good_item = SimpleCatalogItem(
        qualified_identifier="good",
        query_name="good",
        label="good",
        type_label="t",
    )
    original_load_children = cast(
        Callable[[DatabaseTree, CatalogItem], list[CatalogItem]],
        getattr(DatabaseTree._load_children, "__wrapped__"),  # noqa: B009
    )

    @work(thread=True, exit_on_error=False, description="_load_children")
    def patched_load_children(
        tree: DatabaseTree, item: CatalogItem
    ) -> list[CatalogItem]:
        if item is poison_item:
            raise RuntimeError("boom in _load_children")
        return original_load_children(tree, item)

    monkeypatch.setattr(DatabaseTree, "_load_children", patched_load_children)

    app = Harlequin(duckdb_adapter((":memory:",)), connection_hash="child-failure")
    async with app.run_test(size=(120, 36)) as pilot:
        tree = app.data_catalog.database_tree
        while tree.loading or app.editor is None:
            await pilot.pause()

        tree.root.add("poison", data=poison_item)
        tree._add_to_load_queue(poison_item, priority=0)
        for _ in range(200):
            if any(isinstance(screen, ErrorModal) for screen in app.screen_stack):
                break
            await pilot.pause(0.05)
        assert isinstance(app.screen, ErrorModal)
        assert "boom in _load_children" in str(app.screen.error)
        assert app._exception is None
        await pilot.press("space")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        # the loader survived the failed item and still processes the queue
        tree.root.add("good", data=good_item)
        tree._add_to_load_queue(good_item, priority=0)
        for _ in range(200):
            if good_item.loaded:
                break
            await pilot.pause(0.05)
        assert good_item.loaded
        assert len(app.screen_stack) == 1
        assert app._exception is None


@pytest.mark.asyncio
async def test_background_loader_failure_is_surfaced_without_crashing(
    duckdb_adapter: Type[DuckDbAdapter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected failure inside the background loader stops that loader
    with one catalog modal, and does not crash Harlequin.

    Regression test for #1117: the loader used Textual's default
    exit_on_error=True, so anything escaping its loop took the whole app down.
    """

    def raise_on_named_items(tree: DatabaseTree) -> None:
        raise RuntimeError("boom in the loader")

    monkeypatch.setattr(DatabaseTree, "_queue_named_items", raise_on_named_items)
    # the editor's own symbol scans reach _queue_named_items on the message
    # pump (via load_items_named); record their names without queuing, so the
    # raiser above only ever fires from inside the background loader.
    monkeypatch.setattr(
        DatabaseTree,
        "load_items_named",
        lambda tree, names: setattr(
            tree, "_symbol_names", frozenset(name.casefold() for name in names)
        ),
    )
    # no prefetch work either: the loader must still be waiting on an empty
    # queue when the test captures it, not already dead on a queued item.
    monkeypatch.setattr(DatabaseTree, "_schedule_prefetch_scan", lambda self: None)
    app = Harlequin(duckdb_adapter((":memory:",)), connection_hash="loader-failure")
    async with app.run_test(size=(120, 36)) as pilot:
        tree = app.data_catalog.database_tree
        while tree.loading or app.editor is None:
            await pilot.pause()
        for _ in range(200):
            loaders = [
                worker
                for worker in app.workers
                if worker.name == "_database_tree_background_loader"
                and worker.state == WorkerState.RUNNING
            ]
            if loaders:
                break
            await pilot.pause(0.05)
        assert len(loaders) == 1
        loader = loaders[0]

        item = SimpleCatalogItem(
            qualified_identifier="x",
            query_name="x",
            label="x",
            type_label="t",
        )
        tree.root.add("x", data=item)
        tree._add_to_load_queue(item, priority=0)
        for _ in range(200):
            if any(isinstance(screen, ErrorModal) for screen in app.screen_stack):
                break
            await pilot.pause(0.05)
        assert isinstance(app.screen, ErrorModal)
        assert "boom in the loader" in str(app.screen.error)
        assert app._exception is None

        # the failure stopped the loader: it is terminal, not waiting for work
        assert loader.state == WorkerState.ERROR
        assert app.is_running
