from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from textual.message import Message

if TYPE_CHECKING:
    from textual.notifications import SeverityLevel

    from harlequin.app import Harlequin


class HarlequinDriver:
    """
    Provides an interface for Data Catalog interactions to drive
    the Harlequin UI. These will always be called from worker
    threads, so they use Messages to update the main Harlequin
    state.
    """

    class ConfirmAndExecute(Message):
        def __init__(self, callback: Callable[[], None], instructions: str) -> None:
            super().__init__()
            self.callback = callback
            self.instructions = instructions

    class InsertTextAtSelection(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class InsertTextInNewBuffer(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Notify(Message):
        def __init__(self, notify_message: str, severity: "SeverityLevel") -> None:
            super().__init__()
            self.notify_message = notify_message
            self.severity = severity

    class Refreshcatalog(Message):
        pass

    def __init__(self, app: Harlequin) -> None:
        self.app = app

    def insert_text_at_selection(self, text: str) -> None:
        """
        Insert text at the cursor in Harlequin's editor.
        """
        self.app.post_message(self.InsertTextAtSelection(text=text))

    def insert_text_in_new_buffer(self, text: str) -> None:
        """
        Create a new buffer (tab) in Harlequin's editor and insert the text.
        """
        self.app.post_message(self.InsertTextInNewBuffer(text=text))

    def confirm_and_execute(
        self, callback: Callable[[], None], instructions: str = "Are you sure?"
    ) -> None:
        """
        Display a confirmation modal in Harlequin; If the user selects "Yes",
        then Harlequin will invoke the callback.
        """
        self.app.post_message(
            self.ConfirmAndExecute(callback=callback, instructions=instructions)
        )

    def notify(self, message: str, severity: "SeverityLevel" = "information") -> None:
        """
        Show a toast notification in Harlequin.
        """
        self.app.post_message(self.Notify(notify_message=message, severity=severity))

    def refresh_catalog(self) -> None:
        """
        Force a refresh of the catalog.
        """
        self.app.post_message(self.Refreshcatalog())
