from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static


class VerticalSuppressClicks(Vertical):
    def on_click(self, message: events.Click) -> None:
        message.stop()


class HelpScreen(ModalScreen):
    header_text = """
        Harlequin supports a number of key bindings ("shortcuts"). The list below
        includes all of the bindings supported by components of the app.
        You can also view this list at https://harlequin.sh/docs/bindings
    """.split()

    def compose(self) -> ComposeResult:
        markdown_path = Path(__file__).parent / "help_screen.md"
        with open(markdown_path, "r") as f:
            markdown = f.read()

        with VerticalSuppressClicks(id="help_outer"):
            yield Static(" ".join(self.header_text), id="help_header")
            with VerticalScroll(id="help_inner"):
                yield Markdown(markdown=markdown)
            yield Static(
                "Scroll with arrows. Press any other key to continue.", id="help_footer"
            )

    def on_mount(self) -> None:
        container = self.query_one("#help_outer")
        container.border_title = "Harlequin Key Binding Reference"
        self.body = self.query_one("#help_inner")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        if event.key == "up":
            self.body.scroll_up()
        elif event.key == "down":
            self.body.scroll_down()
        elif event.key == "left":
            self.body.scroll_left()
        elif event.key == "right":
            self.body.scroll_right()
        elif event.key == "pageup":
            self.body.scroll_page_up()
        elif event.key == "pagedown":
            self.body.scroll_page_down()
        else:
            self.app.pop_screen()

    def on_click(self) -> None:
        self.app.pop_screen()
