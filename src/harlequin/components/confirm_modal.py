from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label

from harlequin.components.modal import HarlequinModal


class ConfirmModal(HarlequinModal[bool]):
    def __init__(
        self, prompt: str, confirm_label: str = "Yes", cancel_label: str = "No"
    ) -> None:
        super().__init__()
        self.prompt = prompt
        self.confirm_label = confirm_label
        self.cancel_label = cancel_label

    def compose(self) -> ComposeResult:
        with Vertical(id="outer"):
            yield Label(self.prompt, id="prompt_label")
            with Horizontal(id="button_row"):
                yield Button(label=self.cancel_label, id="no")
                yield Button(label=self.confirm_label, variant="primary", id="yes")

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
