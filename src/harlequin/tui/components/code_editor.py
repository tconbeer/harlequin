import re
from typing import List, Union

from sqlfmt.api import Mode, format_string
from sqlfmt.exception import SqlfmtError
from textual.binding import Binding
from textual.message import Message
from textual_textarea import TextArea
from textual_textarea.key_handlers import Cursor
from textual_textarea.serde import serialize_lines
from textual_textarea.textarea import TextInput

from harlequin.tui.components.error_modal import ErrorModal


class CodeEditor(TextArea):
    BINDINGS = [
        Binding(
            "ctrl+enter",
            "submit",
            "Run Query",
            key_display="CTRL+ENTER / CTRL+J",
            show=True,
        ),
        Binding("ctrl+j", "submit", "Run Query", show=False),
        Binding("f4", "format", "Format Query", show=True),
    ]

    class Submitted(Message, bubble=True):
        """Posted when user runs the query.

        Attributes:
            lines: The lines of code being submitted.
            cursor: The position of the cursor
        """

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def on_mount(self) -> None:
        self.border_title = "Query Editor"

    async def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))

    def action_format(self) -> None:
        text_input = self.query_one(TextInput)
        old_cursor = text_input.cursor

        try:
            self.text = format_string(self.text, Mode())
        except SqlfmtError as e:
            self.app.push_screen(
                ErrorModal(
                    title="Formatting Error",
                    header="There was an error while formatting your file:",
                    error=e,
                )
            )
        else:
            text_input.move_cursor(old_cursor.pos, old_cursor.lno)
            text_input.update(text_input._content)

    @property
    def _semicolons(self) -> List[Cursor]:
        semicolons: List[Cursor] = []
        for i, line in enumerate(self.text_input.lines):
            for pos in [m.span()[1] for m in re.finditer(";", line)]:
                semicolons.append(Cursor(i, pos))
        return semicolons

    @property
    def current_query(self) -> str:
        semicolons = self._semicolons
        if semicolons:
            before = Cursor(0, 0)
            after: Union[None, Cursor] = None
            for c in semicolons:
                if c <= self.cursor:
                    before = c
                elif after is None and c > self.cursor:
                    after = c
                    break
            else:
                after = Cursor(
                    len(self.text_input.lines) - 1, len(self.text_input.lines[-1]) - 1
                )
            lines, first, last = self.text_input._get_selected_lines(before, after)
            lines[-1] = lines[-1][: last.pos]
            lines[0] = lines[0][first.pos :]
            return serialize_lines(lines)
        else:
            return self.text
