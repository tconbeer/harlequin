from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class FilenameModal(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="file_container"):
            yield Label("Enter a path to the file:", id="file_header")
            yield Input(id=self.id)
            yield Label("Or press Esc to cancel.", id="file_footer")

    def on_mount(self) -> None:
        input = self.query_one(Input)
        self.set_focus(input)

    def action_cancel(self) -> None:
        self.app.pop_screen()
