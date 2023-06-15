import pytest
from harlequin.tui import Harlequin
from harlequin.tui.components import CodeEditor


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
        editor = app.query_one(CodeEditor)
        editor.text = "select\n\n1 FROM\n\n foo"

        await pilot.press("ctrl+@")  # alias for ctrl+`
        assert editor.text == "select 1 from foo\n"
