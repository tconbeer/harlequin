from __future__ import annotations

from typing import Any, cast

import pyperclip
from rich.markup import escape
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


class VerticalSuppressClicks(Vertical):
    def on_click(self, message: events.Click) -> None:
        message.stop()


class ClickableStatic(Static):
    def on_click(self, message: events.Click) -> None:
        message.stop()
        cast("TextModal", self.screen).copy()


class TextModal(ModalScreen[None]):
    """Base for the modals that show a block of text -- a result cell's whole
    value, an error message -- in a scrollable pane. The arrow and page keys
    scroll it, clicking the text or pressing c copies it, and any other key
    dismisses it, as does a click outside the modal.
    """

    SCROLL_ACTIONS = {
        "up": "scroll_up",
        "down": "scroll_down",
        "left": "scroll_left",
        "right": "scroll_right",
        "pageup": "scroll_page_up",
        "pagedown": "scroll_page_down",
    }
    COPY_NOTIFICATION = "Copied to clipboard."

    def __init__(
        self,
        text: str,
        title: str = "",
        header: str = "",
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.text = text
        self.title = title
        self.header = header

    def compose(self) -> ComposeResult:
        with VerticalSuppressClicks(id="modal_outer"):
            if self.header:
                yield Static(self.header, id="modal_header")
            with VerticalScroll(id="modal_inner"):
                yield ClickableStatic(escape(self.text), id="modal_info")
            yield Static(
                "Arrows/PgUp/PgDn scroll. Click text or press c to copy. "
                "Any other key closes.",
                id="modal_footer",
            )

    def on_mount(self) -> None:
        outer = self.query_one("#modal_outer")
        outer.border_title = self.title
        self.body = self.query_one("#modal_inner", VerticalScroll)

    def on_key(self, event: events.Key) -> None:
        # consume the key so dismissing the modal can't also actuate a binding
        # on the widget underneath (e.g. a results-table shortcut).
        event.stop()
        event.prevent_default()
        scroll_action = self.SCROLL_ACTIONS.get(event.key)
        if scroll_action is not None:
            getattr(self.body, scroll_action)()
        elif event.key == "c":
            self.copy()
        else:
            self.dismiss()

    def on_click(self) -> None:
        # the modal's own container suppresses clicks, so this is a click
        # outside it
        self.dismiss()

    def copy(self) -> None:
        # OSC 52 works over ssh and where pyperclip has no backend
        self.app.copy_to_clipboard(self.text)
        try:
            pyperclip.copy(self.text)
        except pyperclip.PyperclipException:
            pass
        self.app.notify(self.COPY_NOTIFICATION)


class CellViewModal(TextModal):
    """Shows a single result cell's whole value, so long strings and JSON that
    get clipped in the grid can be read in full."""

    COPY_NOTIFICATION = "Cell value copied to clipboard."

    def __init__(
        self,
        value: Any,
        column_label: str = "",
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(
            text="∅ null" if value is None else str(value),
            title=column_label or "Cell Contents",
            name=name,
            id=id,
            classes=classes,
        )


class ErrorModal(TextModal):
    """Shows the message from an exception Harlequin caught."""

    COPY_NOTIFICATION = "Error copied to clipboard."

    def __init__(
        self,
        title: str,
        header: str,
        error: BaseException,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(
            text=str(error),
            title=title,
            header=header,
            name=name,
            id=id,
            classes=classes,
        )
        self.error = error
