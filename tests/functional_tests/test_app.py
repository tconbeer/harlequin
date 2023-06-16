import pytest
from harlequin.tui import Harlequin


@pytest.mark.asyncio
async def test_select_1(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        assert app.title == "Harlequin"
        assert app.focused.__class__.__name__ == "TextInput"

        q = "select 1 as foo"
        for key in q:
            await pilot.press(key)
        await pilot.press("ctrl+j")  # alias for ctrl+enter

        # when the query is submitted, it should update app.query_text, app.relation,
        # and app.data using three different workers.
        await app.workers.wait_for_complete()
        assert app.query_text == q
        await app.workers.wait_for_complete()
        assert app.relation is not None
        await app.workers.wait_for_complete()
        assert app.data == [(1,)]


@pytest.mark.asyncio
async def test_query_formatting(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        app.editor.text = "select\n\n1 FROM\n\n foo"

        await pilot.press("ctrl+@")  # alias for ctrl+`
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
        await app.workers.wait_for_complete()
        assert len(app.data) > 500

        # apply a limit by clicking the limit checkbox
        await pilot.click(bar.checkbox.__class__)
        assert bar.checkbox.value is True
        await pilot.click(bar.button.__class__)
        await app.workers.wait_for_complete()
        assert len(app.data) == 500

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
        await app.workers.wait_for_complete()
        assert len(app.data) == 100


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


@pytest.mark.asyncio
async def test_toggle_full_screen(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        # initialization; all visible
        app.editor.focus()
        assert app.full_screen is False
        assert app.sidebar_hidden is False
        widgets = [app.schema_viewer, app.editor, app.results_viewer]
        for w in widgets:
            assert not w.disabled
            assert w.styles.width
            assert w.styles.width.value > 0

        await pilot.press("f10")
        # only editor visible
        assert not app.editor.disabled
        assert not app.run_query_bar.disabled
        assert app.editor.styles.width
        assert app.editor.styles.width.value > 0
        for w in [w for w in widgets if w != app.editor]:
            assert w.disabled
            assert w.styles.width
            assert w.styles.width.value == 0

        await pilot.press("ctrl+b")
        # editor and schema viewer should be visible
        assert not app.sidebar_hidden
        assert not app.schema_viewer.disabled
        assert app.full_screen
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
        assert not app.editor.disabled

        await pilot.press("f10")
        # only editor visible
        assert not app.editor.disabled
        assert app.schema_viewer.disabled
        assert app.results_viewer.disabled

        await pilot.press("f10")
        # schema viewer should still be hidden
        assert not app.editor.disabled
        assert not app.run_query_bar.disabled
        assert app.schema_viewer.disabled
        assert not app.results_viewer.disabled
        app.editor.text = "select 1"
        await pilot.press("ctrl+j")

        app.results_viewer.focus()
        await pilot.press("f10")
        # only results viewer should be visible
        assert app.editor.disabled
        assert app.run_query_bar.disabled
        assert app.schema_viewer.disabled
        assert not app.results_viewer.disabled

        await pilot.press("ctrl+b")
        # results viewer and schema viewer should be visible
        assert not app.sidebar_hidden
        assert not app.schema_viewer.disabled
        assert app.full_screen
        assert app.editor.disabled
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
