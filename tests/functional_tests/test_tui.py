from pathlib import Path

import pytest
from harlequin.tui import Harlequin
from harlequin.tui.components import TextInput


@pytest.fixture
def app() -> Harlequin:
    return Harlequin(Path(":memory:"))


@pytest.mark.asyncio
async def test_select_1(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        assert app.title == "Harlequin"
        assert app.focused.__class__ == TextInput

        q = "select 1 as foo"
        for key in q:
            await pilot.press(key)
        await pilot.press("ctrl+j")  # alias for ctrl+enter

        # when the query is submitted, it should update app.query_text, app.relation,
        # and app.data using three different workers.
        await app.workers.wait_for_complete()
        assert app.query_text == f"{q} "
        await app.workers.wait_for_complete()
        assert app.relation is not None
        await app.workers.wait_for_complete()
        assert app.data == [(1,)]
