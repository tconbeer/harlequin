from typing import Union

import pyperclip
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


class ClickableStatic(Static):
    def on_click(self, message: events.Click) -> None:
        message.stop()
        try:
            pyperclip.copy(str(self.renderable))
        except pyperclip.PyperclipException:
            pass
        else:
            self.app.notify("Error copied to clipboard.")


class ErrorModal(ModalScreen):
    def __init__(
        self,
        title: str,
        header: str,
        error: BaseException,
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa: A002
        classes: Union[str, None] = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.title = title
        self.header = header
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="error_outer"):
            yield Static(self.header, id="error_header")
            with Vertical(id="error_inner"):
                with VerticalScroll():
                    yield ClickableStatic(str(self.error), id="error_info")
            yield Static(
                "Press any key to continue. Click error to copy.", id="error_footer"
            )

    def on_mount(self) -> None:
        container = self.query_one("#error_outer")
        container.border_title = self.title

    def on_key(self) -> None:
        self.app.pop_screen()

    def on_click(self, message: events.Click) -> None:
        self.app.pop_screen()
