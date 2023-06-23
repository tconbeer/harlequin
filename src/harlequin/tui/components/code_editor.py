from sqlfmt.api import Mode, format_string
from sqlfmt.exception import SqlfmtError
from textual.binding import Binding
from textual.message import Message
from textual_textarea import TextArea
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
