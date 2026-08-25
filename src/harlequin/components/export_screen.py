from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, Sequence, Tuple

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import QueryError
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Select, Static
from textual_fastdatatable.backend import ArrowBackend
from textual_textarea import PathInput

from harlequin.components.results_viewer import ResultsTable
from harlequin.components.text_modal import ErrorModal
from harlequin.exception import HarlequinCopyError
from harlequin.export import names_a_directory, write_file
from harlequin.options import AbstractOption, HarlequinCopyFormat

ExportOptions = Dict[str, Any]


def export_callback(
    screen_data: Tuple[Path, str, ExportOptions],
    table: ResultsTable,
    success_callback: Callable[[], None],
    error_callback: Callable[[Exception], None],
) -> None:
    """Write the visible table to the file the export screen collected.

    The dialog exports every row it fetched, not the rows on screen, so the
    data is `source_data` rather than what the row cap left -- and `source_data`
    already carries the names the cursor reported, duplicates and all, which
    `write_file()` makes unique for duckdb.

    Minus the overflow probe row, where there is one: under the Run Query Bar's
    limit the fetch asks for one row more than the limit, to learn there were
    more, and a file of 501 rows under a limit of 500 is not what was asked for.

    A result with no rows exports as a file with no rows -- a header and
    nothing else, or an empty array. That is a true account of what the query
    returned, and it is what tells a reader "nothing matched" apart from
    "the query failed".
    """
    path, format_name, options = screen_data
    try:
        assert isinstance(table.backend, ArrowBackend)
        data = table.backend.source_data
        if table.fetch_truncated and table.fetched_row_count is not None:
            data = data.slice(0, table.fetched_row_count)
        write_file(
            data=data,
            path=path,
            format_name=format_name,
            options=options,
        )
        success_callback()
    except (OSError, HarlequinCopyError) as e:
        error_callback(e)


def _starting_text(default_path: str | None) -> str:
    """What the path input starts with, for the `-o` the IDE was given.

    A folder gets a trailing separator: what follows it is the file name, and
    the input's autocomplete offers what is already in there.
    """
    if not default_path:
        return ""
    if not names_a_directory(default_path):
        return default_path
    return f"{default_path.rstrip('/' + os.sep)}{os.sep}"


class NoFocusVerticalScroll(VerticalScroll, can_focus=False):
    pass


class CopyOptionsMenu(Widget, can_focus=False):
    def __init__(
        self,
        format_name: str,
        options: Sequence[AbstractOption],
        *children: Widget,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            *children, name=name, id=id, classes=classes, disabled=disabled
        )
        self.format_name = format_name
        self.options = options

    def compose(self) -> ComposeResult:
        for option in self.options:
            with Horizontal(classes="option_row"):
                yield from option.to_widgets()

    def on_mount(self) -> None:
        for option in self.options:
            w = self._get_option_widget_by_name(option.name)
            if w is not None:
                w.tooltip = option.description

    @property
    def current_options(self) -> ExportOptions:
        return {
            option.name: self._get_option_value_by_name(option.name)
            for option in self.options
        }

    def _get_option_widget_by_name(self, name: str) -> Widget | None:
        try:
            return self.query_one(f"#{name}")
        except QueryError:
            return None

    def _get_option_value_by_name(self, name: str) -> Any | None:
        w = self._get_option_widget_by_name(name)
        if w:
            return getattr(w, "value", None)
        else:
            return None


class ExportScreen(ModalScreen[Tuple[Path, str, ExportOptions]]):
    def __init__(
        self,
        formats: list[HarlequinCopyFormat],
        default_path: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.formats = formats
        self.starting_text = _starting_text(default_path)

    def compose(self) -> ComposeResult:
        assert self.formats is not None
        with Vertical(id="export_outer"):
            yield Static(
                "Export the results of your query to a file.",
                id="export_header",
            )
            yield PathInput(
                placeholder=(
                    "/path/to/file  (tab autocompletes, enter exports, esc cancels)"
                ),
                id="path_input",
                file_okay=True,
                dir_okay=False,
                must_exist=False,
                tab_advances_focus=True,
            )
            yield Label("", id="validation_label")
            with Horizontal(classes="option_row"):
                yield Label("Format:", classes="select_label")
                yield Select(
                    options=[(option.label, option.name) for option in self.formats],
                    id="format_select",
                )
            yield NoFocusVerticalScroll(id="options_container")
            with Horizontal(id="export_button_row"):
                yield Button(label="Cancel", variant="error", id="cancel")
                yield Button(label="Export", variant="primary", id="export")

    def on_mount(self) -> None:
        container = self.query_one("#export_outer")
        container.border_title = "Data Exporter"
        self.format_select = self.query_one(Select)
        self.file_input = self.query_one("#path_input", Input)
        self.file_input_validation_label = self.query_one("#validation_label", Label)
        self.options_container = self.query_one(
            "#options_container", NoFocusVerticalScroll
        )
        self.export_button = self.query_one("#export", Button)
        if self.starting_text:
            # assigning posts Input.Changed, so a path with an extension picks
            # its format the way a typed one does, and the cursor lands at the
            # end -- after a folder's separator, ready for a file name
            self.file_input.value = self.starting_text
        self.file_input.focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button
        if button.id == "export":
            self._export()
        else:
            self.app.pop_screen()

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        if event.control.id == "path_input":
            if event.validation_result:
                if event.validation_result.is_valid:
                    self.file_input_validation_label.update("")
                else:
                    self.file_input_validation_label.update(
                        " ".join(event.validation_result.failure_descriptions)
                    )
            old_format = self.format_select.value
            new_format = self._get_format_from_file_extension(event.value)
            if new_format:
                self.format_select.value = new_format
            if self.format_select.value != old_format:
                self.post_message(
                    Select.Changed(self.format_select, value=self.format_select.value)
                )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.control.id == "path_input":
            self.export_button.press()
        else:
            self.focus_next()

    async def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        if event.control.id == "format_select":
            for child in self.options_container.children:
                await child.remove()
            try:
                assert self.formats is not None
                [options] = [
                    fmt.options for fmt in self.formats if fmt.name == event.value
                ]
            except (ValueError, IndexError, AssertionError):
                return
            menu = CopyOptionsMenu(str(event.value), options)
            await self.options_container.mount(menu)

    def _export(self) -> None:
        path = Path(self.file_input.value)
        if path.is_dir():
            self.app.push_screen(
                ErrorModal(
                    title="Error Writing File",
                    header="Path is not a file",
                    error=OSError(f"Cannot write to {path}, since it is a directory."),
                )
            )
        elif self.format_select.value is None:
            self.app.push_screen(
                ErrorModal(
                    title="Error Writing File",
                    header="Must select format",
                    error=OSError(
                        "You must select a file format "
                        f"{[fmt.label for fmt in self.formats]}"
                    ),
                )
            )
        else:
            try:
                options_menu = self.query_one(CopyOptionsMenu)
            except QueryError:
                return
            else:
                self.dismiss(
                    (path, options_menu.format_name, options_menu.current_options)
                )

    def _get_format_from_file_extension(self, input_value: str) -> str | None:
        mapping = {ext: fmt.name for fmt in self.formats for ext in fmt.extensions}
        try:
            p = Path(input_value)
            format_name = mapping[p.suffix]
        except (ValueError, KeyError):
            return None
        else:
            return format_name
