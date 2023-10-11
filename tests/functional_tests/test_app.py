from pathlib import Path
from typing import Callable, List

import pytest
from harlequin import Harlequin
from harlequin.components import ErrorModal, ExportScreen
from harlequin.components.results_viewer import ResultsTable
from textual.geometry import Offset


@pytest.fixture(autouse=True)
def no_use_buffer_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("harlequin.components.code_editor.load_cache", lambda: None)
    monkeypatch.setattr("harlequin.app.write_cache", lambda *_: None)


@pytest.mark.asyncio
async def test_select_1(app: Harlequin, app_snapshot: Callable[..., bool]) -> None:
    async with app.run_test() as pilot:
        assert app.title == "Harlequin"
        assert app.focused.__class__.__name__ == "TextInput"

        q = "select 1 as foo"
        for key in q:
            await pilot.press(key)
        await pilot.press("ctrl+j")  # alias for ctrl+enter

        await pilot.pause()
        assert app.query_text == q
        assert app.cursors
        assert len(app.results_viewer.data) == 1
        assert app.results_viewer.data[
            next(iter(app.results_viewer.data))
        ].to_pylist() == [{"foo": 1}]
        assert app_snapshot(app)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "select 1+1",
        "select 'a' as foo",
        "select null",
        "select null as foo",
        "SELECT {'x': 1, 'y': 2, 'z': 3}",  # struct
        # also a struct:
        "SELECT {'yes': 'duck', 'maybe': 'goose', 'huh': NULL, 'no': 'heron'}",
        "SELECT {'key1': 'string', 'key2': 1, 'key3': 12.345}",  # struct
        """SELECT {'birds':
            {'yes': 'duck', 'maybe': 'goose', 'huh': NULL, 'no': 'heron'},
        'aliens':
            NULL,
        'amphibians':
            {'yes':'frog', 'maybe': 'salamander', 'huh': 'dragon', 'no':'toad'}
        }""",  # struct
        "select {'a': 5} union all select {'a': 6}",  # struct
        "select map {'a': 5}",  # map
        "select map {'a': 5} union all select map {'b': 6}",  # map
        "SELECT map { 1: 42.001, 5: -32.1 }",  # map
        "SELECT map { ['a', 'b']: [1.1, 2.2], ['c', 'd']: [3.3, 4.4] }",  # map
        "SELECT [1, 2, 3]",  # list
        "SELECT ['duck', 'goose', NULL, 'heron'];",  # list
        "SELECT [['duck', 'goose', 'heron'], NULL, ['frog', 'toad'], []];",  # list
    ],
)
async def test_queries_do_not_crash(
    app: Harlequin, query: str, app_snapshot: Callable[..., bool]
) -> None:
    async with app.run_test() as pilot:
        app.editor.text = query
        await pilot.press("ctrl+j")
        await pilot.pause()

        assert app.query_text == query
        assert app.cursors
        assert app_snapshot(app)


@pytest.mark.asyncio
async def test_multiple_queries(
    app: Harlequin, app_snapshot: Callable[..., bool]
) -> None:
    snap_results: List[bool] = []
    async with app.run_test() as pilot:
        q = "select 1; select 2"
        app.editor.text = q
        await pilot.press("ctrl+j")

        # should only run one query
        await pilot.pause()
        assert app.query_text == "select 1;"
        assert len(app.results_viewer.data) == 1
        assert app.results_viewer.data[
            next(iter(app.results_viewer.data))
        ].to_pylist() == [{"1": 1}]
        assert "hide-tabs" in app.results_viewer.classes
        snap_results.append(app_snapshot(app, "One query"))

        app.editor.focus()
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        # should run both queries
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.query_text == "select 1; select 2"
        assert len(app.results_viewer.data) == 2
        assert "hide-tabs" not in app.results_viewer.classes
        snap_results.append(app_snapshot(app, "Both queries"))
        for i, (k, v) in enumerate(app.results_viewer.data.items(), start=1):
            assert v.to_pylist() == [{str(i): i}]
            assert app.query_one(f"#{k}", ResultsTable)
        assert app.results_viewer.tab_switcher.active == "tab-1"
        await pilot.press("k")
        await pilot.wait_for_scheduled_animations()
        assert app.results_viewer.tab_switcher.active == "tab-2"
        snap_results.append(app_snapshot(app, "Both queries, tab 2"))
        await pilot.press("k")
        await pilot.wait_for_scheduled_animations()
        assert app.results_viewer.tab_switcher.active == "tab-1"
        snap_results.append(app_snapshot(app, "Both queries, tab 1"))
        await pilot.press("j")
        assert app.results_viewer.tab_switcher.active == "tab-2"
        await pilot.press("j")
        assert app.results_viewer.tab_switcher.active == "tab-1"

        assert all(snap_results)


@pytest.mark.asyncio
async def test_query_formatting(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        app.editor.text = "select\n\n1 FROM\n\n foo"

        await pilot.press("f4")
        assert app.editor.text == "select 1 from foo\n"


@pytest.mark.asyncio
async def test_run_query_bar(
    app_small_db: Harlequin, app_snapshot: Callable[..., bool]
) -> None:
    snap_results: List[bool] = []
    app = app_small_db
    async with app.run_test(size=(120, 36)) as pilot:
        # initialization
        bar = app.run_query_bar
        assert bar.checkbox.value is False
        assert bar.input.value == "500"
        assert app.limit == 500

        # query without any limit by clicking the button;
        # dataset has 857 records
        app.editor.text = "select * from drivers"
        await pilot.click(bar.button.__class__)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert len(app.results_viewer.data[next(iter(app.results_viewer.data))]) > 500
        snap_results.append(app_snapshot(app, "No limit"))

        # apply a limit by clicking the limit checkbox
        await pilot.click(bar.checkbox.__class__)
        assert bar.checkbox.value is True
        await pilot.click(bar.button.__class__)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert len(app.results_viewer.data[next(iter(app.results_viewer.data))]) == 500
        snap_results.append(app_snapshot(app, "Limit 500"))

        # type an invalid limit, checkbox should be unchecked
        # and a tooltip should appear on hover
        await pilot.click(bar.input.__class__)
        await pilot.press("a")
        assert bar.input.value == "a500"
        assert app.limit == 500
        assert bar.checkbox.value is False
        assert bar.input.tooltip is not None
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        snap_results.append(app_snapshot(app, "Invalid limit"))

        # type a valid limit
        await pilot.press("backspace")
        await pilot.press("delete")
        await pilot.press("1")
        assert bar.input.value == "100"
        assert app.limit == 100
        assert bar.checkbox.value is True
        assert bar.input.tooltip is None

        # run the query with a smaller limit
        await pilot.click(bar.button.__class__)
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert len(app.results_viewer.data[next(iter(app.results_viewer.data))]) == 100
        snap_results.append(app_snapshot(app, "Limit 100"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_toggle_sidebar(
    app: Harlequin, app_snapshot: Callable[..., bool]
) -> None:
    snap_results: List[bool] = []
    async with app.run_test() as pilot:
        # initialization
        sidebar = app.data_catalog
        assert not sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value > 0
        snap_results.append(app_snapshot(app, "Initialization"))

        await pilot.press("ctrl+b")
        assert sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value == 0
        snap_results.append(app_snapshot(app, "Hidden"))

        await pilot.press("ctrl+b")
        assert not sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value > 0
        snap_results.append(app_snapshot(app, "Unhidden"))

        await pilot.press("f9")
        assert sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value == 0
        snap_results.append(app_snapshot(app, "Hidden Again"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_toggle_full_screen(
    app: Harlequin, app_snapshot: Callable[..., bool]
) -> None:
    snap_results: List[bool] = []
    async with app.run_test() as pilot:
        # initialization; all visible
        app.editor.focus()
        assert app.full_screen is False
        assert app.sidebar_hidden is False
        widgets = [app.data_catalog, app.editor_collection, app.results_viewer]
        for w in widgets:
            assert not w.disabled
            assert w.styles.width
            assert w.styles.width.value > 0
        snap_results.append(app_snapshot(app, "Initialization"))

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
        snap_results.append(app_snapshot(app, "Editor Full Screen"))

        await pilot.press("ctrl+b")
        # editor and data catalog should be visible
        assert not app.sidebar_hidden
        assert not app.data_catalog.disabled
        assert app.full_screen
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        snap_results.append(app_snapshot(app, "Editor Full Screen with Sidebar"))

        await pilot.press("f10")
        # all visible
        for w in widgets:
            assert not w.disabled
            assert w.styles.width
            assert w.styles.width.value > 0
        snap_results.append(
            app_snapshot(app, "Exit Full Screen (sidebar already visible)")
        )

        await pilot.press("ctrl+b")
        # data catalog hidden
        assert app.sidebar_hidden
        assert app.data_catalog.disabled
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        snap_results.append(app_snapshot(app, "Sidebar hidden"))

        await pilot.press("f10")
        # only editor visible
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        assert app.data_catalog.disabled
        assert app.results_viewer.disabled
        snap_results.append(
            app_snapshot(app, "Editor Full Screen (sidebar already hidden)")
        )

        await pilot.press("f10")
        # data catalog should still be hidden
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        assert not app.run_query_bar.disabled
        assert app.data_catalog.disabled
        assert not app.results_viewer.disabled
        snap_results.append(
            app_snapshot(app, "Exit Full Screen (sidebar remains hidden)")
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
        snap_results.append(app_snapshot(app, "Results Viewer Full Screen"))

        await pilot.press("f9")
        # results viewer and data catalog should be visible
        assert not app.sidebar_hidden
        assert not app.data_catalog.disabled
        assert app.full_screen
        assert app.editor_collection.disabled
        assert app.run_query_bar.disabled
        assert not app.results_viewer.disabled
        snap_results.append(
            app_snapshot(app, "Results Viewer Full Screen with Sidebar")
        )

        await pilot.press("f10")
        # all visible
        assert not app.sidebar_hidden
        assert not app.full_screen
        for w in widgets:
            assert not w.disabled
            assert w.styles.width
            assert w.styles.width.value > 0
        snap_results.append(app_snapshot(app, "Exit RV Full Screen (sidebar visible)"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_help_screen(app: Harlequin, app_snapshot: Callable[..., bool]) -> None:
    async with app.run_test(size=(120, 36)) as pilot:
        assert len(app.screen_stack) == 1

        await pilot.press("f1")
        assert len(app.screen_stack) == 2
        assert app.screen.id == "help_screen"
        assert app_snapshot(app, "Help Screen")

        await pilot.press("a")  # any key
        assert len(app.screen_stack) == 1

        app.results_viewer.focus()

        await pilot.press("f1")
        assert len(app.screen_stack) == 2

        await pilot.press("space")  # any key
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["one.csv", "one.parquet", "one.json"])
async def test_export(
    app: Harlequin, tmp_path: Path, filename: str, app_snapshot: Callable[..., bool]
) -> None:
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        app.editor.text = "select 1 as a"
        await pilot.press("ctrl+j")  # run query
        assert app.cursors
        assert len(app.screen_stack) == 1

        await pilot.press("ctrl+e")
        assert len(app.screen_stack) == 2
        assert app.screen.id == "export_screen"
        assert isinstance(app.screen, ExportScreen)
        snap_results.append(app_snapshot(app, "Export Screen"))

        app.screen.file_input.value = f"/tmp/foo-bar-static/{filename}"  # type: ignore
        await pilot.pause()
        snap_results.append(app_snapshot(app, "Export with Path"))
        export_path = tmp_path / filename
        app.screen.file_input.value = str(export_path)  # type: ignore
        await pilot.pause()
        await pilot.press("enter")

        assert export_path.is_file()
        assert len(app.screen_stack) == 1
        snap_results.append(app_snapshot(app, "After Export"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_multiple_buffers(
    app: Harlequin, app_snapshot: Callable[..., bool]
) -> None:
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        assert app.editor_collection
        assert app.editor_collection.tab_count == 1
        assert app.editor_collection.active == "tab-1"
        app.editor.text = "tab 1"
        snap_results.append(app_snapshot(app, "Tab 1 of 1 (No tabs)"))

        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 2
        assert app.editor_collection.active == "tab-2"
        assert app.editor.text == ""
        app.editor.text = "tab 2"
        snap_results.append(app_snapshot(app, "Tab 2 of 2"))

        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == ""
        app.editor.text = "tab 3"
        snap_results.append(app_snapshot(app, "Tab 3 of 3"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-1"
        assert app.editor.text == "tab 1"
        snap_results.append(app_snapshot(app, "Tab 1 of 3"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-2"
        assert app.editor.text == "tab 2"
        snap_results.append(app_snapshot(app, "Tab 2 of 3"))

        await pilot.press("ctrl+w")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 2
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == "tab 3"
        # TODO: restore this flaky test (the blue bar appears in the wrong spot)
        # snap_results.append(app_snapshot(app, "Tab 3 after deleting 2"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.active == "tab-1"
        assert app.editor.text == "tab 1"
        snap_results.append(app_snapshot(app, "Tab 1 of [1,3]"))

        await pilot.press("ctrl+k")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == "tab 3"
        snap_results.append(app_snapshot(app, "Tab 3 of [1,3]"))

        assert all(snap_results)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_query",
    [
        "select",  # errors when building cursor
        "select 0::struct(id int)",  # errors when fetching data
        "select; select 0::struct(id int)",  # multiple errors
        "select 1; select 0::struct(id int)",  # one error, mult queries
        "select 0::struct(id int); select 1",  # one error, mult queries, err first
        """
            CREATE TABLE tbl1(u UNION(num INT, str VARCHAR));
            INSERT INTO tbl1 values (1) , ('two') , (union_value(str := 'three'));
            SELECT u FROM tbl1;
        """,  # arrow doesn't do union types.
    ],
)
async def test_query_errors(
    app: Harlequin, bad_query: str, app_snapshot: Callable[..., bool]
) -> None:
    snap_results: List[bool] = []
    async with app.run_test(size=(120, 36)) as pilot:
        app.editor.text = bad_query

        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        assert len(app.screen_stack) == 2
        assert isinstance(app.screen, ErrorModal)
        snap_results.append(app_snapshot(app, "Error visible"))

        await pilot.press("space")
        assert len(app.screen_stack) == 1

        # data table and query bar should be responsive
        assert "non-responsive" not in app.run_query_bar.classes
        assert "non-responsive" not in app.results_viewer.classes
        snap_results.append(app_snapshot(app, "After dismissing error"))

        assert all(snap_results)


@pytest.mark.asyncio
async def test_data_catalog(
    app_multi_db: Harlequin, app_snapshot: Callable[..., bool]
) -> None:
    snap_results: List[bool] = []
    app = app_multi_db
    async with app.run_test(size=(120, 36)) as pilot:
        catalog = app.data_catalog
        assert not catalog.show_root
        snap_results.append(app_snapshot(app, "Initialization"))

        # this test app has two databases attached.
        dbs = catalog.root.children
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
        snap_results.append(app_snapshot(app, "small expanded"))

        # small's second schema is "main". click "main"
        schema_main = dbs[0].children[1]
        await pilot.click(catalog.__class__, offset=Offset(x=8, y=3))
        await pilot.pause()
        assert schema_main.is_expanded is True
        assert catalog.cursor_line == 2  # main is selected
        snap_results.append(app_snapshot(app, "small.main expanded"))

        # ctrl+enter to insert into editor; editor gets focus
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert schema_main.is_expanded is True
        assert app.editor.text == '"small"."main"'
        assert app.editor._has_focus_within
        snap_results.append(app_snapshot(app, "Inserted small.main"))

        # use keys to navigate the tree into main.drivers
        await pilot.press("f6")
        await pilot.pause()
        assert catalog.has_focus
        await pilot.press("down")
        await pilot.press("space")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        col_node = catalog.get_node_at_line(catalog.cursor_line)
        assert col_node is not None
        assert col_node.data is not None
        assert col_node.data.qualified_identifier == '"small"."main"."drivers"."dob"'
        assert col_node.data.query_name == '"dob"'
        snap_results.append(app_snapshot(app, "small.main.drivers.dob selected"))

        # reset the editor, then insert "dob"
        app.editor.text = ""
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert app.editor.text == '"dob"'
        snap_results.append(app_snapshot(app, "small.main.drivers.dob inserted"))

        assert all(snap_results)
