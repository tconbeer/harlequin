from __future__ import annotations

from rich.table import Table
from textual.app import ComposeResult
from textual.widgets import HelpPanel, KeyPanel
from textual.widgets._key_panel import BindingsTable


class KeysTable(BindingsTable):
    """A table of bindings that stops updating while its list has focus."""

    _table: Table | None = None

    def render(self) -> Table:
        # focusing the list would otherwise replace the keys it shows with its own
        if self._table is None or self.screen.focused is not self.parent:
            self._table = self.render_bindings_table()
        return self._table


class KeysList(KeyPanel, can_focus=True):
    """A focusable, scrollable list of the keys bound for the focused widget."""

    # without -textual-system, the app's `*` styles (scrollbars) apply and a
    # click focuses the list.
    DEFAULT_CLASSES = ""

    def compose(self) -> ComposeResult:
        yield KeysTable(shrink=True, expand=False)

    async def recompose(self) -> None:
        if self.screen.focused is self:
            return
        await super().recompose()


class KeysPanel(HelpPanel):
    """A panel listing the keys bound for the focused widget."""

    BORDER_TITLE = "Keys"
    DEFAULT_CLASSES = ""

    def compose(self) -> ComposeResult:
        yield KeysList(id="keys-help")
