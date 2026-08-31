from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from harlequin.components.text_modal import VerticalSuppressClicks


class CellEditModal(ModalScreen[str | None]):
    """Edits one result cell's value, showing the statement that will store it.

    Dismisses with the new value, or with None when the edit is cancelled.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        value: Any,
        column_label: str,
        statement: str,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.value = "" if value is None else str(value)
        self.column_label = column_label
        self.statement = statement

    def compose(self) -> ComposeResult:
        with VerticalSuppressClicks(id="modal_outer"):
            yield Static(escape(self.statement), id="modal_header")
            yield Input(value=self.value, id="modal_input")
            yield Static(
                "Enter runs the update. Escape closes without changing anything.",
                id="modal_footer",
            )

    def on_mount(self) -> None:
        self.query_one("#modal_outer").border_title = self.column_label
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submit(self, message: Input.Submitted) -> None:
        message.stop()
        self.dismiss(message.value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_click(self) -> None:
        # the modal's own container suppresses clicks, so this is a click
        # outside it
        self.dismiss(None)
