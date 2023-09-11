from pathlib import Path

import pytest
from harlequin.tui import Harlequin
from harlequin.tui.components import ErrorModal, ExportScreen
from harlequin.tui.components.results_viewer import ResultsTable
from textual.geometry import Offset


@pytest.fixture(autouse=True)
def no_use_buffer_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("harlequin.tui.components.code_editor.load_cache", lambda: None)
    monkeypatch.setattr("harlequin.tui.app.write_cache", lambda *_: None)


@pytest.mark.asyncio
async def test_select_1(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        assert app.title == "Harlequin"
        assert app.focused.__class__.__name__ == "TextInput"

        q = "select 1 as foo"
        for key in q:
            await pilot.press(key)
        await pilot.press("ctrl+j")  # alias for ctrl+enter

        await pilot.pause()
        assert app.query_text == q
        assert app.relations
        assert len(app.results_viewer.data) == 1
        assert app.results_viewer.data[next(iter(app.results_viewer.data))] == [(1,)]


@pytest.mark.asyncio
async def test_multiple_queries(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        q = "select 1; select 2"
        app.editor.text = q
        await pilot.press("ctrl+j")

        # should only run one query
        await pilot.pause()
        assert app.query_text == "select 1;"
        assert len(app.results_viewer.data) == 1
        assert app.results_viewer.data[next(iter(app.results_viewer.data))] == [(1,)]
        assert "hide-tabs" in app.results_viewer.classes

        app.editor.focus()
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        # should run both queries
        await pilot.pause()
        assert app.query_text == "select 1; select 2"
        assert len(app.results_viewer.data) == 2
        assert "hide-tabs" not in app.results_viewer.classes
        for i, (k, v) in enumerate(app.results_viewer.data.items(), start=1):
            assert v == [(i,)]
            assert app.query_one(f"#{k}", ResultsTable)
        assert app.results_viewer.tab_switcher.active == "tab-1"
        await pilot.press("k")
        assert app.results_viewer.tab_switcher.active == "tab-2"
        await pilot.press("k")
        assert app.results_viewer.tab_switcher.active == "tab-1"
        await pilot.press("j")
        assert app.results_viewer.tab_switcher.active == "tab-2"
        await pilot.press("j")
        assert app.results_viewer.tab_switcher.active == "tab-1"


@pytest.mark.asyncio
async def test_query_formatting(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        app.editor.text = "select\n\n1 FROM\n\n foo"

        await pilot.press("f4")
        assert app.editor.text == "select 1 from foo\n"


@pytest.mark.asyncio
async def test_run_query_bar(app_small_db: Harlequin) -> None:
    app = app_small_db
    async with app.run_test() as pilot:
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
        assert len(app.results_viewer.data[next(iter(app.results_viewer.data))]) > 500

        # apply a limit by clicking the limit checkbox
        await pilot.click(bar.checkbox.__class__)
        assert bar.checkbox.value is True
        await pilot.click(bar.button.__class__)
        await pilot.pause()
        assert len(app.results_viewer.data[next(iter(app.results_viewer.data))]) == 500

        # type an invalid limit, checkbox should be unchecked
        # and a tooltip should appear on hover
        await pilot.click(bar.input.__class__)
        await pilot.press("a")
        assert bar.input.value == "a500"
        assert app.limit == 500
        assert bar.checkbox.value is False
        assert bar.input.tooltip is not None

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
        assert len(app.results_viewer.data[next(iter(app.results_viewer.data))]) == 100


@pytest.mark.asyncio
async def test_toggle_sidebar(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        # initialization
        sidebar = app.schema_viewer
        assert not sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value > 0

        await pilot.press("ctrl+b")
        assert sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value == 0

        await pilot.press("ctrl+b")
        assert not sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value > 0

        await pilot.press("f9")
        assert sidebar.disabled
        assert sidebar.styles.width
        assert sidebar.styles.width.value == 0


@pytest.mark.asyncio
async def test_toggle_full_screen(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        # initialization; all visible
        app.editor.focus()
        assert app.full_screen is False
        assert app.sidebar_hidden is False
        widgets = [app.schema_viewer, app.editor_collection, app.results_viewer]
        for w in widgets:
            assert not w.disabled
            assert w.styles.width
            assert w.styles.width.value > 0

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

        await pilot.press("ctrl+b")
        # editor and schema viewer should be visible
        assert not app.sidebar_hidden
        assert not app.schema_viewer.disabled
        assert app.full_screen
        assert not app.editor_collection.disabled
        assert not app.editor.disabled

        await pilot.press("f10")
        # all visible
        for w in widgets:
            assert not w.disabled
            assert w.styles.width
            assert w.styles.width.value > 0

        await pilot.press("ctrl+b")
        # schema viewer hidden
        assert app.sidebar_hidden
        assert app.schema_viewer.disabled
        assert not app.editor_collection.disabled
        assert not app.editor.disabled

        await pilot.press("f10")
        # only editor visible
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        assert app.schema_viewer.disabled
        assert app.results_viewer.disabled

        await pilot.press("f10")
        # schema viewer should still be hidden
        assert not app.editor_collection.disabled
        assert not app.editor.disabled
        assert not app.run_query_bar.disabled
        assert app.schema_viewer.disabled
        assert not app.results_viewer.disabled
        app.editor.text = "select 1"
        await pilot.press("ctrl+j")

        app.results_viewer.focus()
        await pilot.press("f10")
        # only results viewer should be visible
        assert app.editor_collection.disabled
        assert app.run_query_bar.disabled
        assert app.schema_viewer.disabled
        assert not app.results_viewer.disabled

        await pilot.press("f9")
        # results viewer and schema viewer should be visible
        assert not app.sidebar_hidden
        assert not app.schema_viewer.disabled
        assert app.full_screen
        assert app.editor_collection.disabled
        assert app.run_query_bar.disabled
        assert not app.results_viewer.disabled

        await pilot.press("f10")
        # all visible
        assert not app.sidebar_hidden
        assert not app.full_screen
        for w in widgets:
            assert not w.disabled
            assert w.styles.width
            assert w.styles.width.value > 0


@pytest.mark.asyncio
async def test_help_screen(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        assert len(app.screen_stack) == 1

        await pilot.press("f1")
        assert len(app.screen_stack) == 2
        assert app.screen.id == "help_screen"

        await pilot.press("a")  # any key
        assert len(app.screen_stack) == 1

        app.results_viewer.focus()

        await pilot.press("f1")
        assert len(app.screen_stack) == 2

        await pilot.press("space")  # any key
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["one.csv", "one.parquet", "one.json"])
async def test_export(app: Harlequin, tmp_path: Path, filename: str) -> None:
    async with app.run_test() as pilot:
        app.editor.text = "select 1 as a"
        await pilot.press("ctrl+j")  # run query
        assert app.relations
        assert len(app.screen_stack) == 1

        await pilot.press("ctrl+e")
        assert len(app.screen_stack) == 2
        assert app.screen.id == "export_screen"
        assert isinstance(app.screen, ExportScreen)
        export_path = tmp_path / filename
        app.screen.file_input.value = str(export_path)  # type: ignore
        await pilot.press("enter")

        assert export_path.is_file()
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_multiple_buffers(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        assert app.editor_collection
        assert app.editor_collection.tab_count == 1
        assert app.editor_collection.active == "tab-1"
        app.editor.text = "tab 1"

        await pilot.press("ctrl+n")
        await pilot.pause()
        assert app.editor_collection.tab_count == 2
        assert app.editor_collection.active == "tab-2"
        assert app.editor.text == ""
        app.editor.text = "tab 2"

        await pilot.press("ctrl+n")
        await pilot.pause()
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == ""
        app.editor.text = "tab 3"

        await pilot.press("ctrl+k")
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-1"
        assert app.editor.text == "tab 1"

        await pilot.press("ctrl+k")
        assert app.editor_collection.tab_count == 3
        assert app.editor_collection.active == "tab-2"
        assert app.editor.text == "tab 2"

        await pilot.press("ctrl+w")
        assert app.editor_collection.tab_count == 2
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == "tab 3"

        await pilot.press("ctrl+k")
        assert app.editor_collection.active == "tab-1"
        assert app.editor.text == "tab 1"

        await pilot.press("ctrl+k")
        assert app.editor_collection.active == "tab-3"
        assert app.editor.text == "tab 3"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_query",
    [
        "select",  # errors when building relation
        "select 0::struct(id int)",  # errors when fetching data
        "select; select 0::struct(id int)",  # multiple errors
    ],
)
async def test_query_errors(app: Harlequin, bad_query: str) -> None:
    async with app.run_test() as pilot:
        app.editor.text = bad_query

        await pilot.press("ctrl+j")
        assert len(app.screen_stack) == 2
        assert isinstance(app.screen, ErrorModal)

        await pilot.press("space")
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_data_catalog(app_multi_db: Harlequin) -> None:
    app = app_multi_db
    async with app.run_test() as pilot:
        catalog = app.schema_viewer
        assert not catalog.show_root

        # this test app has two databases attached.
        dbs = catalog.root.children
        assert len(dbs) == 2

        # the first db is called "small"
        assert str(dbs[0].label) == "small"
        assert dbs[0].data is not None
        assert dbs[0].data.qualified_identifier == '"small"'
        assert dbs[0].data.query_name == '"small"'
        assert dbs[0].is_expanded is False

        # the small db has two schemas, but you can't see them yet
        assert len(dbs[0].children) == 2
        assert all(not node.is_expanded for node in dbs[0].children)

        assert str(dbs[1].label) == "tiny"
        assert dbs[0].is_expanded is False

        # click on "small" and see it expand.
        await pilot.click(catalog.__class__, offset=Offset(x=6, y=1))
        assert dbs[0].is_expanded is True
        assert dbs[1].is_expanded is False
        assert all(not node.is_expanded for node in dbs[0].children)

        # small's second schema is "main". click "main"
        schema_main = dbs[0].children[1]
        await pilot.click(catalog.__class__, offset=Offset(x=8, y=3))
        await pilot.pause()
        assert schema_main.is_expanded is True
        assert catalog.cursor_line == 2  # main is selected

        # ctrl+enter to insert into editor; editor gets focus
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert schema_main.is_expanded is True
        assert app.editor.text == '"small"."main"'
        assert app.editor._has_focus_within

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

        # reset the editor, then insert "dob"
        app.editor.text = ""
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert app.editor.text == '"dob"'
