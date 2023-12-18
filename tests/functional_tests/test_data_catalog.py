from typing import Awaitable, Callable, List

import pytest
from harlequin import Harlequin
from textual.geometry import Offset


@pytest.mark.asyncio
async def test_data_catalog(
    app_multi_duck: Harlequin, app_snapshot: Callable[..., Awaitable[bool]]
) -> None:
    snap_results: List[bool] = []
    app = app_multi_duck
    async with app.run_test(size=(120, 36)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        catalog = app.data_catalog
        assert not catalog.show_root
        snap_results.append(await app_snapshot(app, "Initialization"))

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
        snap_results.append(await app_snapshot(app, "small expanded"))

        # small's second schema is "main". click "main"
        schema_main = dbs[0].children[1]
        await pilot.click(catalog.__class__, offset=Offset(x=8, y=3))
        await pilot.pause()
        assert schema_main.is_expanded is True
        assert catalog.cursor_line == 2  # main is selected
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
        snap_results.append(await app_snapshot(app, "small.main.drivers.dob selected"))

        # reset the editor, then insert "dob"
        app.editor.text = ""
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert app.editor.text == '"dob"'
        snap_results.append(await app_snapshot(app, "small.main.drivers.dob inserted"))

        assert all(snap_results)
