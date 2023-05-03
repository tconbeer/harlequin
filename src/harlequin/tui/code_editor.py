from textual.binding import Binding
from textual.widgets import Input


class CodeEditor(Input):
    BINDINGS = [
        ("ctrl+enter", "submit", "Run Query"),
        Binding("ctrl+j", "submit", "Run Query", show=False),
        Binding("enter", "newline", "", show=False),
    ]

    def on_mount(self) -> None:
        self.border_title = "Code Editor"

    def action_newline(self) -> None:
        self.insert_text_at_cursor("\n")
