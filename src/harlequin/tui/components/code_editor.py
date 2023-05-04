from sqlfmt.api import Mode, format_string
from textual.binding import Binding
from textual.widgets import Input

from harlequin.tui.components.filename_modal import FilenameModal


class CodeEditor(Input):
    BINDINGS = [
        ("ctrl+enter", "submit", "Run Query"),
        Binding("ctrl+j", "submit", "Run Query", show=False),
        ("ctrl+`", "format", "Format Query"),
        Binding("ctrl+@", "format", "Format Query", show=False),
        ("ctrl+s", "save", "Save Query"),
        ("ctrl+o", "load", "Open Query"),
        Binding("enter", "newline", "", show=False),
    ]

    def on_mount(self) -> None:
        self.border_title = "Query Editor"

    def action_newline(self) -> None:
        self.insert_text_at_cursor("\n")

    def action_format(self) -> None:
        formatted = format_string(self.value, Mode())
        self.value = formatted
        self.action_end()

    def action_save(self) -> None:
        self.app.push_screen(FilenameModal(id="save_modal"))

    def action_load(self) -> None:
        self.app.push_screen(FilenameModal(id="load_modal"))
