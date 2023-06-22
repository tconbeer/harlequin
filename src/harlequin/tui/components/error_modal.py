from typing import Union

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


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
        self.title = title
        self.header = header
        self.error = error
        super().__init__(name, id, classes)

    def compose(self) -> ComposeResult:
        with Vertical(id="outer"):
            yield Static(self.header, id="error_header")
            with Vertical(id="inner"):
                with VerticalScroll():
                    yield Static(str(self.error), id="error_info")
            yield Static("Press any key to continue.", id="error_footer")

    def on_mount(self) -> None:
        container = self.query_one("#outer")
        container.border_title = self.title

    def on_key(self) -> None:
        self.app.pop_screen()
