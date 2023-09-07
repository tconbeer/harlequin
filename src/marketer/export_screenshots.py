import asyncio

from harlequin import Harlequin
from pygments.styles import get_all_styles


async def save_all_screenshots() -> None:
    for theme in get_all_styles():
        print(f"Screenshotting {theme}")
        app = Harlequin(["f1.db"], theme=theme)
        async with app.run_test(size=(120, 36)) as pilot:
            app.editor.text = "select *\nfrom drivers"
            app.schema_viewer.root.expand()
            for child in app.schema_viewer.root.children:
                child.expand()
                for grandchild in child.children:
                    grandchild.expand()
                    for great in grandchild.children:
                        if str(great.label).startswith("drivers"):
                            great.expand()
            app.schema_viewer.cursor_line = 7
            await pilot.press("ctrl+j")
            app.save_screenshot(filename=f"{theme}.svg", path="./static/themes/")
        app.exit()


asyncio.run(save_all_screenshots())
