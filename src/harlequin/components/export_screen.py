from pathlib import Path
from typing import Callable, Tuple

import duckdb
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Select, Static, Switch
from textual_textarea import PathInput

from harlequin.components.error_modal import ErrorModal
from harlequin.duck_ops import export_relation
from harlequin.export_options import (
    CSVOptions,
    ExportOptions,
    JSONOptions,
    ParquetOptions,
)


def export_callback(
    screen_data: Tuple[Path, ExportOptions],
    relation: duckdb.DuckDBPyRelation,
    connection: duckdb.DuckDBPyConnection,
    success_callback: Callable[[], None],
    error_callback: Callable[[Exception], None],
) -> None:
    try:
        export_relation(
            relation=relation,
            connection=connection,
            path=screen_data[0],
            options=screen_data[1],
        )
        success_callback()
    except (OSError, duckdb.Error) as e:
        error_callback(e)


class NoFocusLabel(Label, can_focus=False):
    pass


class NoFocusVerticalScroll(VerticalScroll, can_focus=False):
    pass


class OptionsMenu(Widget, can_focus=False):
    @property
    def current_options(self) -> ExportOptions:
        raise NotImplementedError()


class CSVOptionsMenu(OptionsMenu):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Header:", classes="switch_label")
            yield Switch(id="header")
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Separator:", classes="input_label")
            yield Input(value=",", id="sep")
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Compression:", classes="select_label")
            yield Select(
                options=[
                    ("Auto", "auto"),
                    ("gzip", "gzip"),
                    ("zstd", "zstd"),
                    ("No compression", "none"),
                ],
                allow_blank=False,
                value="auto",
                id="compression",
            )
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Force Quote:", classes="switch_label")
            yield Switch(id="force_quote")
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Date Format:", classes="input_label")
            yield Input(value="", placeholder="%Y-%m-%d", id="dateformat")
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Timestamp Format:", classes="input_label")
            yield Input(value="", placeholder="%c", id="timestampformat")
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Quote Char:", classes="input_label")
            yield Input(value='"', id="quote")
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Escape Char:", classes="input_label")
            yield Input(value='"', id="escape")
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Null String:", classes="input_label")
            yield Input(value="", id="nullstr")
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Encoding:", classes="input_label")
            yield Input(value="UTF8", id="encoding")

    @property
    def current_options(self) -> CSVOptions:
        return CSVOptions(
            header=self.header.value,
            sep=self.sep.value,
            compression=self.compression.value,  # type: ignore
            force_quote=self.force_quote.value,
            dateformat=self.dateformat.value,
            timestampformat=self.timestampformat.value,
            quote=self.quote.value,
            escape=self.escape.value,
            nullstr=self.nullstr.value,
            encoding=self.encoding.value,
        )

    def on_mount(self) -> None:
        self.header = self.query_one("#header", Switch)
        self.header.tooltip = "Switch on to include column name headers."
        self.sep = self.query_one("#sep", Input)
        self.sep.tooltip = "The separator (or delimeter) between cols in each row."
        self.compression = self.query_one("#compression", Select)
        self.compression.tooltip = (
            "The compression type for the file. By default this will be detected "
            "automatically from the file extension (e.g. file.csv.gz will use gzip, "
            "file.csv will use no compression)."
        )
        self.force_quote = self.query_one("#force_quote", Switch)
        self.force_quote.tooltip = "Switch on to always quote all strings."
        self.dateformat = self.query_one("#dateformat", Input)
        self.dateformat.tooltip = "Specifies the date format to use when writing dates."
        self.timestampformat = self.query_one("#timestampformat", Input)
        self.timestampformat.tooltip = (
            "Specifies the date format to use when writing timestamps."
        )
        self.quote = self.query_one("#quote", Input)
        self.quote.tooltip = (
            "The quoting character to be used when a data value is quoted."
        )
        self.escape = self.query_one("#escape", Input)
        self.escape.tooltip = (
            "The character that should appear before a character that matches the "
            "quote value."
        )
        self.nullstr = self.query_one("#nullstr", Input)
        self.nullstr.tooltip = "The string that is written to represent a NULL value."
        self.encoding = self.query_one("#encoding", Input)
        self.encoding.tooltip = "Only UTF8 is currently supported by DuckDB.s"


class ParquetOptionsMenu(OptionsMenu):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Compression:", classes="select_label")
            yield Select(
                options=[("Snappy", "snappy"), ("gzip", "gzip"), ("zstd", "zstd")],
                allow_blank=False,
                value="snappy",
                id="compression",
            )
        # not yet supported in python API
        # with Horizontal(classes="option_row"):
        #     yield NoFocusLabel("Row Group Size:", classes="input_label")
        #     yield Input(
        #         value='122880',
        #         validators=Integer(minimum=1),
        #         id="row_group_size"
        #     )
        # with Horizontal(classes="option_row"):
        #     yield NoFocusLabel("Field IDs:", classes="input_label")
        #     yield Input(value='', id="field_ids")

    @property
    def current_options(self) -> ParquetOptions:
        return ParquetOptions(
            compression=self.compression.value,  # type: ignore
            # not yet supported in python API
            # row_group_size = int(self.row_group_size.value),
            # field_ids = self.field_ids.value,
        )

    def on_mount(self) -> None:
        self.compression = self.query_one("#compression", Select)
        # not yet supported in python API
        # self.row_group_size = self.query_one("#row_group_size", Input)
        # self.field_ids = self.query_one("#field_ids", Input)


class JSONOptionsMenu(OptionsMenu):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Array:", classes="switch_label")
            yield Switch(id="array")
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Compression:", classes="select_label")
            yield Select(
                options=[
                    ("Auto", "auto"),
                    ("gzip", "gzip"),
                    ("zstd", "zstd"),
                    ("No compression", "uncompressed"),
                ],
                allow_blank=False,
                value="auto",
                id="compression",
            )
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Date Format:", classes="input_label")
            yield Input(value="", placeholder="%Y-%m-%d", id="dateformat")
        with Horizontal(classes="option_row"):
            yield NoFocusLabel("Timestamp Format:", classes="input_label")
            yield Input(value="", placeholder="%c", id="timestampformat")

    @property
    def current_options(self) -> JSONOptions:
        return JSONOptions(
            array=self.array.value,
            compression=self.compression.value,  # type: ignore
            dateformat=self.dateformat.value,
            timestampformat=self.timestampformat.value,
        )

    def on_mount(self) -> None:
        self.array = self.query_one("#array", Switch)
        self.array.tooltip = (
            "Whether to write a JSON array. If true, a JSON array of records is "
            "written, if false, newline-delimited JSON is written."
        )
        self.compression = self.query_one("#compression", Select)
        self.compression.tooltip = (
            "The compression type for the file. By default this will be detected "
            "automatically from the file extension (e.g. file.json.gz will use gzip, "
            "file.json will use no compression)."
        )
        self.dateformat = self.query_one("#dateformat", Input)
        self.dateformat.tooltip = "Specifies the date format to use when writing dates."
        self.timestampformat = self.query_one("#timestampformat", Input)
        self.timestampformat.tooltip = (
            "Specifies the date format to use when writing timestamps."
        )


class ExportScreen(ModalScreen[Tuple[Path, ExportOptions]]):
    def compose(self) -> ComposeResult:
        with Vertical(id="export_outer"):
            yield Static(
                "Export the results of your query to a CSV, Parquet, or JSON file.",
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
                    options=[("CSV", "csv"), ("Parquet", "parquet"), ("JSON", "json")],
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
        self.options_container = self.query_one("#options_container", VerticalScroll)
        self.export_button = self.query_one("#export", Button)
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
            try:
                p = Path(event.value)
                if p.suffix in (".parquet", ".pq"):
                    self.format_select.value = "parquet"
                elif p.suffix in (".csv", ".tsv"):
                    self.format_select.value = "csv"
                elif p.suffix in (".json", ".js", ".ndjson"):
                    self.format_select.value = "json"
            except ValueError:
                pass
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
            for t in [CSVOptionsMenu, ParquetOptionsMenu, JSONOptionsMenu]:
                w = self.query(t)
                if w:
                    await w.remove()
            if event.value == "csv":
                await self.options_container.mount(CSVOptionsMenu())
            elif event.value == "parquet":
                await self.options_container.mount(ParquetOptionsMenu())
            elif event.value == "json":
                await self.options_container.mount(JSONOptionsMenu())

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
                        "You must select a file format (CSV, Parquet, or JSON)"
                    ),
                )
            )
        else:
            options_menu = self.query_one(OptionsMenu)
            self.dismiss((path, options_menu.current_options))
