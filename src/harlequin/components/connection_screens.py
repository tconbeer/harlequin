"""Modals for opening, switching, closing, and saving a database connection.

The screens are given the adapter names and the labels. They do not import
an adapter, and they do not open a database.
"""

from __future__ import annotations

from typing import Sequence

from rich.markup import escape
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, OptionList, Select, Static

from harlequin.components.modal import HarlequinModal
from harlequin.connections import profile_name_error


class OpenConnectionScreen(HarlequinModal[tuple[str, str]]):
    """Asks which adapter and connection string to open. Empty string is the default."""

    def __init__(
        self,
        adapter_names: Sequence[str],
        current: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.adapter_names = list(adapter_names)
        self.current = current if current in self.adapter_names else None

    def compose(self) -> ComposeResult:
        with Vertical(id="connection_outer"):
            yield Static(
                "Open another database. This connection stays open.",
                id="connection_prompt",
            )
            yield Label("Adapter")
            yield Select(
                [(name, name) for name in self.adapter_names],
                value=self.current or self.adapter_names[0],
                allow_blank=False,
                id="connection_adapter",
            )
            yield Label("Connection string")
            yield Input(
                placeholder="Leave empty for the adapter's default database",
                id="connection_string",
            )
            yield Label("", id="connection_error")
            with Horizontal(id="connection_buttons"):
                yield Button("Cancel", id="connection_cancel")
                yield Button("Connect", variant="primary", id="connection_connect")

    def on_mount(self) -> None:
        self.query_one("#connection_outer").border_title = "Open Connection"
        self.query_one("#connection_string", Input).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss()

    @on(Button.Pressed, "#connection_cancel")
    def cancel(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#connection_connect")
    @on(Input.Submitted, "#connection_string")
    def connect(self) -> None:
        select = self.query_one("#connection_adapter", Select)
        adapter = select.value
        if not isinstance(adapter, str) or adapter not in self.adapter_names:
            self.query_one("#connection_error", Label).update("Choose an adapter.")
            return
        raw = self.query_one("#connection_string", Input).value
        self.dismiss((adapter, raw))


class ConnectionListScreen(HarlequinModal[tuple[str, int]]):
    """The open connections. Switch runs queries on one. Close drops one."""

    def __init__(
        self,
        labels: Sequence[str],
        active: int,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.labels = list(labels)
        self.active = active if 0 <= active < len(self.labels) else 0

    def compose(self) -> ComposeResult:
        with Vertical(id="connection_outer"):
            yield Static(
                "Switch runs queries on a connection. Close drops one.",
                id="connection_prompt",
            )
            yield OptionList(
                *[escape(label) for label in self.labels],
                id="connection_list",
            )
            with Horizontal(id="connection_buttons"):
                yield Button("Cancel", id="connection_cancel")
                yield Button("Close", id="connection_close")
                yield Button("Switch", variant="primary", id="connection_switch")

    def on_mount(self) -> None:
        self.query_one("#connection_outer").border_title = "Connections"
        listing = self.query_one("#connection_list", OptionList)
        if self.labels:
            listing.highlighted = self.active
        listing.focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss()

    def _highlighted(self) -> int | None:
        highlighted = self.query_one("#connection_list", OptionList).highlighted
        if highlighted is None or not 0 <= highlighted < len(self.labels):
            return None
        return highlighted

    @on(Button.Pressed, "#connection_cancel")
    def cancel(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#connection_switch")
    def switch(self) -> None:
        self._choose("switch")

    @on(Button.Pressed, "#connection_close")
    def close(self) -> None:
        self._choose("close")

    @on(OptionList.OptionSelected, "#connection_list")
    def switch_from_list(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(("switch", event.option_index))

    def _choose(self, action: str) -> None:
        index = self._highlighted()
        if index is None:
            return
        self.dismiss((action, index))


class SaveConnectionScreen(HarlequinModal[str]):
    """Asks for the profile name this connection is saved under."""

    def compose(self) -> ComposeResult:
        with Vertical(id="connection_outer"):
            yield Static(
                "Save this connection as a profile, with the keymaps in use.",
                id="connection_prompt",
            )
            yield Input(placeholder="Profile name", id="connection_profile")
            yield Label("", id="connection_error")
            with Horizontal(id="connection_buttons"):
                yield Button("Cancel", id="connection_cancel")
                yield Button("Save", variant="primary", id="connection_save")

    def on_mount(self) -> None:
        self.query_one("#connection_outer").border_title = "Save Connection"
        self.query_one("#connection_profile", Input).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss()

    @on(Button.Pressed, "#connection_cancel")
    def cancel(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#connection_save")
    @on(Input.Submitted, "#connection_profile")
    def save(self) -> None:
        name = self.query_one("#connection_profile", Input).value.strip()
        error = profile_name_error(name)
        if error is not None:
            self.query_one("#connection_error", Label).update(error)
            return
        self.dismiss(name)
