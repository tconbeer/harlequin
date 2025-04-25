from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static


class VerticalSuppressClicks(Vertical):
    def on_click(self, message: events.Click) -> None:
        message.stop()


class AdapterDetailsScreen(ModalScreen):
    def __init__(
        self,
        details: str,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.details = details

    header_text = """
        Harlequin can display details about the adapter collected from client + server.
    """.split()

    def compose(self) -> ComposeResult:
        markdown = f"""{self.details}"""

        with VerticalSuppressClicks(id="adapter_outer"):
            yield Static(" ".join(self.header_text), id="adapter_header")
            with VerticalScroll(id="adapter_inner"):
                yield Markdown(markdown=markdown)
            yield Static(
                "Scroll with arrows. Press any other key to continue.",
                id="adapter_footer",
            )

    def on_mount(self) -> None:
        container = self.query_one("#adapter_outer")
        container.border_title = "Harlequin Adapter Details"
        self.body = self.query_one("#adapter_inner")

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
