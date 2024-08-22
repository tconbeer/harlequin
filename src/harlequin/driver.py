from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from textual.notifications import SeverityLevel

    from harlequin.app import Harlequin


class HarlequinDriver:
    """
    Provides an interface for Data Catalog interactions to drive
    the Harlequin UI.
    """

    def __init__(self, app: Harlequin) -> None:
        self.app = app

    def insert_text_at_cursor(self, text: str) -> None: ...

    def insert_text_in_new_buffer(self, text: str, run_query: bool = False) -> None: ...

    def show_confirmation(
        self, callback: Callable[[None], None], instructions: str = "Are you sure?"
    ) -> None:
        """
        Display a confirmation modal in Harlequin; If the user selects "Proceed",
        then Harlequin will invoke the callback.
        """
        ...

    def notify(self, message: str, severity: "SeverityLevel" = "information") -> None:
        """
        Show a toast notification in Harlequin
        """
        self.app.notify(message=message, severity=severity)
