from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmModal(ModalScreen[bool]):
    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="outer"):
            yield Label(self.prompt, id="prompt_label")
            with Horizontal(id="button_row"):
                yield Button(label="No", id="no")
                yield Button(label="Yes", variant="primary", id="yes")

    @on(Button.Pressed, "#yes")
    def save_from_button(self) -> None:
        self.action_continue()

    @on(Button.Pressed, "#no")
    def cancel_from_button(self) -> None:
        self.action_cancel()

    def action_continue(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
