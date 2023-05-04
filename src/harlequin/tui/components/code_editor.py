from sqlfmt.api import Mode, format_string
from textual.binding import Binding
from textual.widgets import Input
from rich.highlighter import Highlighter
from rich.segment import Segment
from textual.widgets._input import _InputRenderable

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

    def __init__(
        self,
        value: str | None = None,
        placeholder: str = "",
        highlighter: Highlighter | None = None,
        password: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        # The default input widget crops the input when it gets too long.
        # We don't want this! This monkeypatches the method so it doesn't
        # do any cropping
        def line_crop(  # type: ignore
            segments: list[Segment],
            *args,
            **kwargs,
        ) -> list[Segment]:
            return segments

        _InputRenderable.__rich_console__.__globals__["line_crop"] = line_crop
        super().__init__(
            value, placeholder, highlighter, password, name, id, classes, disabled
        )

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
