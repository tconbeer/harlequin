from __future__ import annotations

from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static

from harlequin.components.text_modal import VerticalSuppressClicks


class HelpScreen(ModalScreen):
    header_text = """
        Welcome to Harlequin! This screen contains a small subset of the online
        docs, available at https://harlequin.sh/docs/getting-started
    """.split()

    def __init__(
        self,
        keys_panel_key: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.keys_panel_key = keys_panel_key

    def compose(self) -> ComposeResult:
        markdown_path = Path(__file__).parent / "help_screen.md"
        with open(markdown_path, "r") as f:
            markdown = f.read()

        with VerticalSuppressClicks(id="modal_outer"):
            yield Static(" ".join(self.header_text), id="modal_header")
            yield Static(self._keys_panel_tip(), id="keys_panel_tip")
            with VerticalScroll(id="modal_inner"):
                yield Markdown(markdown=markdown)
            yield Static(
                "Scroll with arrows. Press any other key to continue.",
                id="modal_footer",
            )

    def _keys_panel_tip(self) -> str:
        if self.keys_panel_key is None:
            return (
                "To see the keys for the focused widget, open the Keys panel from "
                "the command palette ([b $secondary]ctrl+p[/])."
            )
        return (
            f"Press [b $secondary]{self.keys_panel_key}[/] to show or hide the "
            "Keys panel, which lists the keys for the focused widget."
        )

    def on_mount(self) -> None:
        container = self.query_one("#modal_outer")
        container.border_title = "Harlequin Help"
        self.body = self.query_one("#modal_inner")

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
