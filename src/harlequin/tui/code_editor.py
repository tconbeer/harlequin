from rich.highlighter import Highlighter
from textual.widgets import Input
from textual.binding import Binding


class CodeEditor(Input):
    BINDINGS = [
        ("ctrl+enter", "submit", "Run Query"),
        Binding("ctrl+j", "submit", "Run Query", show=False),
        Binding("enter", "newline", "", show=False),
    ]

    def action_newline(self) -> None:
        self.insert_text_at_cursor("\n")
