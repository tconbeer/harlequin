"""Modals that ask which directory or S3 location the Data Catalog shows."""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Static
from textual_textarea import PathInput

from harlequin.components.data_catalog.s3_tree import s3_uri_error
from harlequin.components.modal import HarlequinModal


class CatalogSourceScreen(HarlequinModal[str]):
    """Asks for a location for one of the Data Catalog's tabs.

    Dismisses with the location, or with None if the user cancels. An invalid
    location keeps the modal open with the reason under the input.
    """

    MODAL_TITLE: ClassVar[str] = ""
    PROMPT: ClassVar[str] = ""

    def __init__(
        self,
        current_location: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.current_location = current_location

    def compose(self) -> ComposeResult:
        with Vertical(id="catalog_source_outer"):
            yield Static(self.PROMPT, id="catalog_source_prompt")
            yield self.compose_location_input()
            yield Label("", id="catalog_source_error")
            with Horizontal(id="catalog_source_button_row"):
                yield Button(label="Cancel", id="cancel")
                yield Button(label="Show", variant="primary", id="show")

    def compose_location_input(self) -> Input:
        raise NotImplementedError

    def location_error(self, location: str) -> str | None:
        """Why `location` cannot be shown, or None if it can."""
        raise NotImplementedError

    def normalize(self, location: str) -> str:
        return location

    def on_mount(self) -> None:
        self.query_one("#catalog_source_outer").border_title = self.MODAL_TITLE
        self.location_input = self.query_one("#catalog_source_input", Input)
        self.error_label = self.query_one("#catalog_source_error", Label)
        if self.current_location:
            self.location_input.value = self.current_location
        self.location_input.focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss()

    @on(PathInput.Cancelled)
    def cancel_from_input(self, event: PathInput.Cancelled) -> None:
        event.stop()
        self.dismiss()

    @on(Button.Pressed, "#show")
    def show_from_button(self) -> None:
        self.submit()

    @on(Button.Pressed, "#cancel")
    def cancel_from_button(self) -> None:
        self.dismiss()

    @on(Input.Submitted, "#catalog_source_input")
    def show_from_input(self, event: Input.Submitted) -> None:
        event.stop()
        self.submit()

    @on(Input.Changed, "#catalog_source_input")
    def clear_error(self, event: Input.Changed) -> None:
        event.stop()
        self.error_label.update("")

    def submit(self) -> None:
        location = self.location_input.value.strip()
        error = self.location_error(location)
        if error is not None:
            self.error_label.update(error)
            return
        self.dismiss(self.normalize(location))


class FilesSourceScreen(CatalogSourceScreen):
    MODAL_TITLE = "Show Files"
    PROMPT = "Show a directory in the Data Catalog's Files tab."

    def compose_location_input(self) -> Input:
        # the error label reports what is wrong, on submit, not while typing
        return PathInput(
            placeholder="/path/to/dir  (tab autocompletes, enter shows, esc cancels)",
            id="catalog_source_input",
            dir_okay=True,
            file_okay=True,
            must_exist=False,
        )

    def on_mount(self) -> None:
        # a trailing separator, so autocomplete offers what is inside
        if self.current_location:
            self.current_location = (
                f"{self.current_location.rstrip('/' + os.sep)}{os.sep}"
            )
        super().on_mount()

    def location_error(self, location: str) -> str | None:
        if not location:
            return "Enter a directory."
        path = Path(location).expanduser()
        if not path.exists():
            return f"{location} does not exist."
        if not path.is_dir():
            return f"{location} is not a directory."
        return None

    def normalize(self, location: str) -> str:
        return str(Path(location).expanduser())


class S3SourceScreen(CatalogSourceScreen):
    MODAL_TITLE = "Show S3"
    PROMPT = (
        "Show a bucket in the Data Catalog's S3 tab, as my-bucket, "
        "s3://my-bucket/my-prefix, an https:// URL, or all for every bucket."
    )

    def compose_location_input(self) -> Input:
        return Input(
            placeholder="s3://my-bucket/my-prefix  (enter shows, esc cancels)",
            id="catalog_source_input",
        )

    def location_error(self, location: str) -> str | None:
        return s3_uri_error(location)
