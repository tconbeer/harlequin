from textual import events
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Vertical, VerticalScroll


class ErrorModal(ModalScreen):

    def __init__(
        self, error: Exception, name: str | None = None, id: str | None = None, classes: str | None = None
    ) -> None:
        self.error = error
        super().__init__(name, id, classes)

    def compose(self) -> ComposeResult:
        with Vertical(id="outer"):
            yield Static("DuckDB raised an error when compiling or running your query:", id="error_header")
            with Vertical(id = "inner"):
                with VerticalScroll():
                    yield Static(str(self.error), id="error_info")
            yield Static("Press any key to continue.", id="error_footer")

    def on_mount(self) -> None:
        container = self.query_one("#outer")
        container.border_title = "DuckDB Error"

    def on_key(self) -> None:
        self.app.pop_screen()