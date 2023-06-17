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
