from textual.binding import Binding
from textual.message import Message

from harlequin.tui.components.textarea import TextArea, TextInput


class CodeEditor(TextArea):
    BINDINGS = [
        ("ctrl+enter", "submit", "Run Query"),
        Binding("ctrl+j", "submit", "Run Query", show=False),
    ]

    class Submitted(Message, bubble=True):
        """Posted when user runs the query.

        Attributes:
            lines: The lines of code being submitted.
            cursor: The position of the cursor
        """

        def __init__(self, lines: list[str], cursor: tuple[int, int]) -> None:
            super().__init__()
            self.lines: list[str] = lines
            self.cursor: tuple[int, int] = cursor

    async def action_submit(self) -> None:
        input = self.query_one(TextInput)
        self.post_message(self.Submitted(input.lines, input.cursor))
