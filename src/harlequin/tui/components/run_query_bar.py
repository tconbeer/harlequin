from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Checkbox


class RunQueryBar(Horizontal):
    DEFAULT_CSS = """
        RunQueryBar {
            height: 1;
            width: 100%;
        }
    """

    def compose(self) -> ComposeResult:
        yield Checkbox("Limit 500")
        yield Button("Run Query", id="run_query")

    def on_mount(self) -> None:
        self.checkbox = self.query_one(Checkbox)
