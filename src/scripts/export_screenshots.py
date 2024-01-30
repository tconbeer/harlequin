import asyncio

from harlequin import Harlequin
from harlequin_duckdb import DuckDbAdapter
from pygments.styles import get_all_styles

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


async def save_all_screenshots() -> None:
    adapter = DuckDbAdapter(("f1.db",), no_init=True)
    for theme in get_all_styles():
        print(f"Screenshotting {theme}")
        app = Harlequin(adapter=adapter, theme=theme)
        async with app.run_test(size=(120, 36)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            if app.editor is None:
                await pilot.pause(0.2)
            assert app.editor is not None
            app.editor.text = TEXT
            app.editor.cursor = (9, 16)  # type: ignore
            app.editor.selection_anchor = (9, 0)  # type: ignore
            app.data_catalog.database_tree.root.expand()
            for child in app.data_catalog.database_tree.root.children:
                print("here!")
                child.expand()
                for grandchild in child.children:
                    grandchild.expand()
                    for great in grandchild.children:
                        if str(great.label).startswith("drivers"):
                            great.expand()
            app.data_catalog.database_tree.cursor_line = 7
            await pilot.press("ctrl+j")
            app.save_screenshot(filename=f"{theme}.svg", path="./static/themes/")
        app.exit()


asyncio.run(save_all_screenshots())
