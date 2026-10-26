"""Ask whether to save a query tab, close it without saving, or keep it."""

from __future__ import annotations

from enum import Enum

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.widgets import Button, Label

from harlequin.components.modal import HarlequinModal


class CloseChoice(Enum):
    """What the user chose for a tab that has unsaved text."""

    SAVE = "save"
    DISCARD = "discard"
    CANCEL = "cancel"


class CloseBufferModal(HarlequinModal[CloseChoice]):
    """Save, close without saving, or keep the tab open.

    The prompt is literal text. A path may contain brackets.
    """

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="outer"):
            yield Label(
                Content.from_text(self.prompt, markup=False),
                id="close_buffer_prompt",
            )
            with Horizontal(id="button_row"):
                yield Button(label="Cancel", id="close_buffer_cancel")
                yield Button(label="Don't Save", id="close_buffer_discard")
                yield Button(
                    label="Save",
                    variant="primary",
                    id="close_buffer_save",
                )

    def on_mount(self) -> None:
        self.query_one("#close_buffer_cancel", Button).focus()

    @on(Button.Pressed, "#close_buffer_save")
    def save_from_button(self) -> None:
        self.dismiss(CloseChoice.SAVE)

    @on(Button.Pressed, "#close_buffer_discard")
    def discard_from_button(self) -> None:
        self.dismiss(CloseChoice.DISCARD)

    @on(Button.Pressed, "#close_buffer_cancel")
    def cancel_from_button(self) -> None:
        self.dismiss(CloseChoice.CANCEL)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(CloseChoice.CANCEL)
