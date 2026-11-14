"""A HarlequinDriver for catalog interactions run without an app."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from harlequin.driver import HarlequinDriver

if TYPE_CHECKING:
    from textual.notifications import SeverityLevel


class RecordingDriver(HarlequinDriver):
    """Keeps the text an interaction inserts, and confirms whatever it asks to."""

    def __init__(self) -> None:
        self.new_buffers: list[str] = []
        self.inserted_at_selection: list[str] = []
        self.notifications: list[tuple[str, str]] = []
        self.refreshed_catalog = False

    def insert_text_at_selection(self, text: str) -> None:
        self.inserted_at_selection.append(text)

    def insert_text_in_new_buffer(self, text: str) -> None:
        self.new_buffers.append(text)

    def confirm_and_execute(
        self, callback: Callable[[], None], instructions: str = "Are you sure?"
    ) -> None:
        callback()

    def notify(self, message: str, severity: SeverityLevel = "information") -> None:
        self.notifications.append((message, severity))

    def refresh_catalog(self) -> None:
        self.refreshed_catalog = True
