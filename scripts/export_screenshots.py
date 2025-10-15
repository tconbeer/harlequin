import asyncio

from textual.widgets.text_area import Selection
from textual.widgets.tree import TreeNode

from harlequin import Harlequin
from harlequin.catalog import CatalogItem
from harlequin.colors import VALID_THEMES
from harlequin_duckdb import DuckDbAdapter

TEXT = """
select
    drivers.surname,
    drivers.forename,
    drivers.nationality,
    avg(driver_standings.position) as avg_standing,
    avg(driver_standings.points) as avg_points
from driver_standings
join drivers on driver_standings.driverid = drivers.driverid
join races on driver_standings.raceid = races.raceid
group by 1, 2, 3
order by avg_standing asc
""".strip()


async def wait_for_filtered_workers(app: Harlequin) -> None:
    filtered_workers = [
        w for w in app.workers if w.name != "_database_tree_background_loader"
    ]
    if filtered_workers:
        await app.workers.wait_for_complete(filtered_workers)


async def save_all_screenshots() -> None:
    adapter = DuckDbAdapter(("f1.db",), no_init=True)
    for theme in VALID_THEMES:
        print(f"Screenshotting {theme}")
        app = Harlequin(adapter=adapter, theme=theme)
        async with app.run_test(size=(120, 36)) as pilot:
            await wait_for_filtered_workers(app)
            await pilot.pause()
            if app.editor is None:
                await pilot.pause(0.2)
            assert app.editor is not None
            app.editor.text = TEXT
            app.editor.selection = Selection((9, 0), (9, 16))

            async def _expand_and_wait(node: TreeNode[CatalogItem]) -> None:
                node.expand()
                while not node.children:
                    if getattr(node.data, "loaded", True):
                        break
                    await pilot.pause()

            app.data_catalog.database_tree.root.expand()
            for db_node in app.data_catalog.database_tree.root.children:
                await _expand_and_wait(db_node)
                for schema_node in db_node.children:
                    await _expand_and_wait(schema_node)
                    for table_node in schema_node.children:
                        if str(table_node.label).startswith("drivers"):
                            await _expand_and_wait(table_node)
            app.data_catalog.database_tree.cursor_line = 7
            await pilot.press("ctrl+j")
            app.save_screenshot(filename=f"{theme}.svg", path="./static/themes/")
        app.exit()


def main() -> None:
    asyncio.run(save_all_screenshots())


if __name__ == "__main__":
    main()
