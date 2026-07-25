from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


class CellEditModal(ModalScreen[str | None]):
    """Edit a single result cell's value and confirm the UPDATE.

    Dismisses with the new value (a string) when the user runs the update, or
    with ``None`` if they cancel. The caller is responsible for building and
    executing the actual UPDATE statement.
    """

    DEFAULT_CSS = """
    CellEditModal {
        align: center middle;
    }
    CellEditModal > #cell-edit-outer {
        width: 60%;
        max-width: 90;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    CellEditModal #cell-edit-title {
        text-style: bold;
        width: 100%;
    }
    CellEditModal #cell-edit-target {
        color: $text-muted;
        width: 100%;
        margin-bottom: 1;
    }
    CellEditModal #cell-edit-input {
        margin-bottom: 1;
    }
    CellEditModal #cell-edit-buttons {
        height: auto;
        align-horizontal: right;
    }
    CellEditModal #cell-edit-buttons Button {
        margin-left: 2;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        table: str,
        column: str,
        where_description: str,
        current: object,
    ) -> None:
        super().__init__()
        self._table = table
        self._column = column
        self._where_description = where_description
        # NULLs are shown as an empty field; the raw value is otherwise stringified.
        self._current = "" if current is None else str(current)

    def compose(self) -> ComposeResult:
        with Vertical(id="cell-edit-outer"):
            yield Label(f"Edit {self._table}.{self._column}", id="cell-edit-title")
            yield Label(
                f"update {self._table} set {self._column} = … "
                f"where {self._where_description}",
                id="cell-edit-target",
            )
            yield Input(value=self._current, id="cell-edit-input")
            with Horizontal(id="cell-edit-buttons"):
                yield Button("Cancel", id="cell-edit-cancel")
                yield Button("Run update", variant="primary", id="cell-edit-run")

    def on_mount(self) -> None:
        self.query_one("#cell-edit-input", Input).focus()

    @on(Input.Submitted, "#cell-edit-input")
    def _submit_from_input(self) -> None:
        self._run()

    @on(Button.Pressed, "#cell-edit-run")
    def _submit_from_button(self) -> None:
        self._run()

    @on(Button.Pressed, "#cell-edit-cancel")
    def _cancel_from_button(self) -> None:
        self.action_cancel()

    def _run(self) -> None:
        self.dismiss(self.query_one("#cell-edit-input", Input).value)

    def action_cancel(self) -> None:
        self.dismiss(None)
