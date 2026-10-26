"""Save an unbound query by browsing to a folder and typing a file name."""

from __future__ import annotations

import os
from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.widgets import Button, DirectoryTree, Input, Label, Static, Tree

from harlequin.components.folder_browser import FolderBrowser
from harlequin.components.modal import HarlequinModal


class TypeAPath:
    """The user wants the one-line path prompt instead of the tree."""


class SaveQueryScreen(HarlequinModal[Path | TypeAPath]):
    """Browse to a folder and type only the file name.

    The folder does not have to be one the Files tab is showing. Dismisses
    with the file path, with `TypeAPath` when the user wants to type a whole
    path, or with None when they cancel.
    """

    def __init__(
        self,
        mounts: list[Path],
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self._mounts = list(mounts)
        self.file_tree = FolderBrowser(self._mounts[0], id="save_query_tree")

    def compose(self) -> ComposeResult:
        with Vertical(id="save_query_outer"):
            yield Static(
                "Browse to a folder, then name the file.",
                id="save_query_prompt",
            )
            yield self.file_tree
            yield Input(
                placeholder="File name",
                id="save_query_name",
            )
            yield Label("", id="save_query_destination")
            yield Label("", id="save_query_error")
            with Horizontal(id="save_query_button_row"):
                yield Button(label="Cancel", id="save_query_cancel")
                yield Button(label="Up", id="save_query_up")
                yield Button(label="Type a path", id="save_query_type_path")
                yield Button(label="Save", variant="primary", id="save_query_save")

    def on_mount(self) -> None:
        self.query_one("#save_query_outer").border_title = "Save Query"
        self.query_one("#save_query_name", Input).focus()
        self._show_destination()

    def chosen_directory(self) -> Path | None:
        """The folder the tree cursor is on, or that file's parent."""
        return self.file_tree.cursor_directory()

    def _clean_name(self, raw: str) -> str | None:
        name = raw.strip()
        if not name or name in {".", ".."}:
            return None
        if os.sep in name or "/" in name or "\\" in name:
            return None
        return name

    def _show_destination(self) -> None:
        directory = self.chosen_directory()
        name = self.query_one("#save_query_name", Input).value.strip()
        if directory is None:
            shown = ""
        elif name:
            shown = str(directory / name)
        else:
            shown = str(directory)
        self.query_one("#save_query_destination", Label).update(
            Content.from_text(shown, markup=False)
        )

    def _show_error(self, message: str) -> None:
        self.query_one("#save_query_error", Label).update(message)

    @on(DirectoryTree.FileSelected, "#save_query_tree")
    def keep_file_in_the_dialog(self, message: DirectoryTree.FileSelected) -> None:
        """A file in this tree names the save. It does not open a query tab."""
        message.stop()
        self.query_one("#save_query_name", Input).value = message.path.name
        self._show_destination()

    @on(Input.Changed, "#save_query_name")
    def destination_follows_the_name(self, event: Input.Changed) -> None:
        event.stop()
        self._show_error("")
        self._show_destination()

    @on(Input.Submitted, "#save_query_name")
    def save_from_name(self, event: Input.Submitted) -> None:
        event.stop()
        self.submit_save()

    @on(Button.Pressed, "#save_query_save")
    def save_from_button(self) -> None:
        self.submit_save()

    @on(Button.Pressed, "#save_query_cancel")
    def cancel_from_button(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#save_query_up")
    def browse_up(self) -> None:
        self.file_tree.action_browse_up()

    @on(Button.Pressed, "#save_query_type_path")
    def type_a_path(self) -> None:
        self.dismiss(TypeAPath())

    @on(Tree.NodeHighlighted, "#save_query_tree")
    def destination_follows_the_cursor(self, event: Tree.NodeHighlighted) -> None:
        event.stop()
        self._show_destination()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss()

    def submit_save(self) -> None:
        directory = self.chosen_directory()
        if directory is None:
            self._show_error("Choose a folder.")
            return
        name = self._clean_name(self.query_one("#save_query_name", Input).value)
        if name is None:
            self._show_error("Enter a file name.")
            return
        self.dismiss(directory / name)
