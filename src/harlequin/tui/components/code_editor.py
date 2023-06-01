from textual.binding import Binding
from textual.message import Message
from textual_textarea import TextArea


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

    async def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))
