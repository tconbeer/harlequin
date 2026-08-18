from __future__ import annotations

from typing import Any

import pyperclip
from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


class CellViewModal(ModalScreen[None]):
    """Shows a single result cell's whole value in a scrollable pane, so long
    strings and JSON that get clipped in the grid can be read in full."""

    BINDINGS = [
        Binding("escape,enter,q", "close", "Close"),
        Binding("c", "copy", "Copy Value"),
    ]

    def __init__(
        self,
        value: Any,
        column_label: str = "",
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.value_str = "∅ null" if value is None else str(value)
        self.column_label = column_label

    def compose(self) -> ComposeResult:
        with Vertical(id="cell_view_outer"):
            with VerticalScroll(id="cell_view_scroll"):
                yield Static(escape(self.value_str), id="cell_view_value")
            yield Static(
                "Arrows / PgUp / PgDn scroll. c copies. esc closes.",
                id="cell_view_footer",
            )

    def on_mount(self) -> None:
        outer = self.query_one("#cell_view_outer")
        outer.border_title = self.column_label or "Cell Contents"
        scroll = self.query_one("#cell_view_scroll", VerticalScroll)
        scroll.can_focus = True
        scroll.focus()

    def action_close(self) -> None:
        self.dismiss()

    def action_copy(self) -> None:
        # OSC 52 works over ssh and where pyperclip has no backend
        self.app.copy_to_clipboard(self.value_str)
        try:
            pyperclip.copy(self.value_str)
        except pyperclip.PyperclipException:
            pass
        self.app.notify("Cell value copied to clipboard.")
