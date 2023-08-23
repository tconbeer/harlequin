import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Type, Union

import duckdb
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.stylesheet import Stylesheet
from textual.dom import DOMNode
from textual.driver import Driver
from textual.reactive import reactive
from textual.types import CSSPathType
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Footer, Input
from textual.worker import WorkerFailed, get_current_worker

from harlequin.colors import HarlequinColors
from harlequin.duck_ops import connect, get_catalog
from harlequin.exception import HarlequinExit
from harlequin.tui.components import (
    CodeEditor,
    CSVOptions,
    EditorCollection,
    ErrorModal,
    ExportOptions,
    ExportScreen,
    HelpScreen,
    JSONOptions,
    ParquetOptions,
    ResultsViewer,
    RunQueryBar,
    SchemaViewer,
)


class Harlequin(App, inherit_bindings=False):
    """
    A Textual App for a SQL client for DuckDB.
    """

    CSS_PATH = "app.tcss"
    MAX_RESULTS = 10_000

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("f1", "show_help_screen", "Help"),
        Binding("f2", "focus_query_editor", "Focus Query Editor", show=False),
        Binding("f5", "focus_results_viewer", "Focus Results Viewer", show=False),
        Binding("f6", "focus_data_catalog", "Focus Data Catalog", show=False),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar", show=False),
        Binding("f9", "toggle_sidebar", "Toggle Sidebar", show=False),
        Binding("f10", "toggle_full_screen", "Toggle Full Screen Mode", show=False),
        Binding("ctrl+e", "export", "Export Data", show=False),
    ]

    query_text: reactive[str] = reactive(str)
    selection_text: reactive[str] = reactive(str)
    relations: reactive[Dict[str, duckdb.DuckDBPyRelation]] = reactive(dict)
    full_screen: reactive[bool] = reactive(False)
    sidebar_hidden: reactive[bool] = reactive(False)

    def __init__(
        self,
        db_path: Sequence[Union[str, Path]],
        read_only: bool = False,
        allow_unsigned_extensions: bool = False,
        extensions: Union[List[str], None] = None,
        force_install_extensions: bool = False,
        custom_extension_repo: Union[str, None] = None,
        theme: str = "monokai",
        md_token: Union[str, None] = None,
        md_saas: bool = False,
        driver_class: Union[Type[Driver], None] = None,
        css_path: Union[CSSPathType, None] = None,
        watch_css: bool = False,
    ):
        super().__init__(driver_class, css_path, watch_css)
        self.theme = theme
        self.limit = 500
        try:
            self.app_colors = HarlequinColors.from_theme(theme)
            self.connection = connect(
                db_path,
                read_only=read_only,
                allow_unsigned_extensions=allow_unsigned_extensions,
                extensions=extensions,
                force_install_extensions=force_install_extensions,
                custom_extension_repo=custom_extension_repo,
                md_token=md_token,
                md_saas=md_saas,
            )
        except HarlequinExit:
            self.exit()
        else:
            self.design = self.app_colors.design_system
            self.stylesheet = Stylesheet(variables=self.get_css_variables())

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        with Horizontal():
            yield SchemaViewer(
                "Data Catalog",
                connection=self.connection,
                type_color=self.app_colors.gray,
            )
            with Vertical(id="main_panel"):
                yield EditorCollection(language="sql", theme=self.theme)
                yield RunQueryBar(max_results=self.MAX_RESULTS)
                yield ResultsViewer(
                    max_results=self.MAX_RESULTS, type_color=self.app_colors.gray
                )
        yield Footer()

    async def on_mount(self) -> None:
        self.schema_viewer = self.query_one(SchemaViewer)
        self.editor_collection = self.query_one(EditorCollection)
        self.editor = self.editor_collection.current_editor
        self.results_viewer = self.query_one(ResultsViewer)
        self.run_query_bar = self.query_one(RunQueryBar)
        self.footer = self.query_one(Footer)

        self.set_focus(self.editor)
        self.run_query_bar.checkbox.value = False

        worker = self.update_schema_data()
        await worker.wait()

    def _set_query_text(self) -> None:
        self.query_text = self._validate_selection() or self.editor.current_query

    def on_editor_collection_editor_switched(
        self, message: EditorCollection.EditorSwitched
    ) -> None:
        if message.active_editor is not None:
            self.editor = message.active_editor
        else:
            self.editor = self.editor_collection.current_editor

    def on_code_editor_submitted(self, message: CodeEditor.Submitted) -> None:
        message.stop()
        self._set_query_text()

    def on_button_pressed(self, message: Button.Pressed) -> None:
        message.stop()
        if message.control.id == "run_query":
            self._set_query_text()

    def on_text_area_cursor_moved(self) -> None:
        self.selection_text = self._validate_selection()

    def _validate_selection(self) -> str:
        """
        If the selection is valid query, return it. Otherwise
        return the empty string.
        """
        selection = self.editor.selected_text
        if selection:
            escaped = selection.replace("'", "''")
            try:
                (parsed,) = self.connection.sql(  # type: ignore
                    f"select json_serialize_sql('{escaped}')"
                ).fetchone()
            except Exception:
                return ""
            result = json.loads(parsed)
            # DDL statements return an error of type "not implemented"
            if result.get("error", True) and result.get("error_type", "") == "parser":
                return ""
            else:
                return selection
        else:
            return ""

    def on_checkbox_changed(self, message: Checkbox.Changed) -> None:
        """
        invalidate the last query so we re-run the query with the limit
        """
        if message.checkbox.id == "limit_checkbox":
            message.stop()
            self.query_text = ""

    def on_input_changed(self, message: Input.Changed) -> None:
        """
        invalidate the last query so we re-run the query with the limit
        """
        if message.input.id == "limit_input":
            message.stop()
            if (
                message.input.value
                and message.validation_result
                and message.validation_result.is_valid
            ):
                self.query_text = ""
                self.limit = int(message.input.value)
                message.input.tooltip = None
            elif message.validation_result:
                failures = "\n".join(message.validation_result.failure_descriptions)
                message.input.tooltip = (
                    f"[{self.app_colors.error}]Validation Error:[/]\n{failures}"
                )

    def on_input_submitted(self, message: Input.Submitted) -> None:
        if message.input.id == "limit_input":
            message.stop()
            if (
                message.input.value
                and message.validation_result
                and message.validation_result.is_valid
            ):
                self._set_query_text()

    def on_results_viewer_ready(self, message: ResultsViewer.Ready) -> None:
        message.stop()
        self.run_query_bar.set_responsive()

    def action_export(self) -> None:
        def export_data(screen_data: Tuple[Path, ExportOptions]) -> None:
            assert self.relations, "Internal error! Could not export relation (None)"
            active_table = self.results_viewer.get_visible_table()
            if active_table is None or active_table.id is None:
                return
            relation = self.relations[active_table.id]
            raw_path = screen_data[0]
            options = screen_data[1]
            path = str(raw_path.expanduser())
            try:
                if isinstance(options, CSVOptions):
                    relation.write_csv(
                        file_name=path,
                        sep=options.sep,
                        na_rep=options.nullstr,
                        header=options.header,
                        quotechar=options.quote,
                        escapechar=options.escape,
                        date_format=options.dateformat if options.dateformat else None,
                        timestamp_format=options.timestampformat
                        if options.timestampformat
                        else None,
                        quoting="ALL" if options.force_quote else None,
                        compression=options.compression,
                        encoding=options.encoding,
                    )
                elif isinstance(options, ParquetOptions):
                    relation.write_parquet(
                        file_name=path, compression=options.compression
                    )
                elif isinstance(options, JSONOptions):
                    compression = (
                        f", COMPRESSION {options.compression}"
                        if options.compression in ("gzip", "zstd", "uncompressed")
                        else ""
                    )
                    print("compression: ", compression)
                    date_format = (
                        f", DATEFORMAT {options.dateformat}"
                        if options.dateformat
                        else ""
                    )
                    ts_format = (
                        f", TIMESTAMPFORMAT {options.timestampformat}"
                        if options.timestampformat
                        else ""
                    )
                    self.connection.sql(
                        f"copy ({relation.sql_query()}) to '{path}' "
                        "(FORMAT JSON"
                        f"{', ARRAY TRUE' if options.array else ''}"
                        f"{compression}{date_format}{ts_format}"
                        ")"
                    )
            except (OSError, duckdb.InvalidInputException, duckdb.BinderException) as e:
                self.app.push_screen(
                    ErrorModal(
                        title="Export Data Error",
                        header=("Could not export data."),
                        error=e,
                    )
                )

        if not self.relations:
            self.app.push_screen(
                ErrorModal(
                    title="Export Data Error",
                    header=("Could not export data."),
                    error=ValueError(
                        "There is no data to export. Run the query first."
                    ),
                )
            )
        else:
            self.app.push_screen(ExportScreen(id="export_screen"), export_data)

    def action_focus_query_editor(self) -> None:
        self.editor.focus()

    def action_focus_results_viewer(self) -> None:
        self.results_viewer.focus()

    def action_focus_data_catalog(self) -> None:
        if self.sidebar_hidden or self.schema_viewer.disabled:
            self.action_toggle_sidebar()
        self.schema_viewer.focus()

    def action_toggle_sidebar(self) -> None:
        """
        sidebar_hidden and self.sidebar.disabled both hold important state.
        The sidebar can be hidden with either ctrl+b or f10, and we need
        to persist the state depending on how that happens
        """
        if self.sidebar_hidden is False and self.schema_viewer.disabled is True:
            # sidebar was hidden by f10; toggle should show it
            self.schema_viewer.disabled = False
        else:
            self.sidebar_hidden = not self.sidebar_hidden

    def action_toggle_full_screen(self) -> None:
        self.full_screen = not self.full_screen

    def action_show_help_screen(self) -> None:
        self.push_screen(HelpScreen(id="help_screen"))

    def watch_full_screen(self, full_screen: bool) -> None:
        full_screen_widgets = [self.editor_collection, self.results_viewer]
        other_widgets = [self.run_query_bar, self.footer]
        all_widgets = [*full_screen_widgets, *other_widgets]
        if full_screen:
            target: Optional[DOMNode] = self.focused
            while target not in full_screen_widgets:
                if (
                    target is None
                    or target in other_widgets
                    or not isinstance(target, Widget)
                ):
                    return
                else:
                    target = target.parent
            for w in all_widgets:
                w.disabled = w != target
            if target == self.editor_collection:
                self.run_query_bar.disabled = False
            self.schema_viewer.disabled = True
        else:
            for w in all_widgets:
                w.disabled = False
            self.schema_viewer.disabled = self.sidebar_hidden

    def watch_sidebar_hidden(self, sidebar_hidden: bool) -> None:
        if sidebar_hidden:
            if self.schema_viewer.has_focus:
                self.editor.focus()
        self.schema_viewer.disabled = sidebar_hidden

    def watch_selection_text(self, selection_text: str) -> None:
        if selection_text:
            self.run_query_bar.button.label = "Run Selection"
        else:
            self.run_query_bar.button.label = "Run Query"

    async def watch_query_text(self, query_text: str) -> None:
        if query_text:
            self.full_screen = False
            self.run_query_bar.set_not_responsive()
            self.results_viewer.show_loading()
            try:
                worker = self._build_relation(query_text)
                await worker.wait()
            except WorkerFailed as e:
                self.run_query_bar.set_responsive()
                self.results_viewer.set_responsive()
                self.results_viewer.show_table()
                self.push_screen(
                    ErrorModal(
                        title="DuckDB Error",
                        header=(
                            "DuckDB raised an error when compiling "
                            "or running your query:"
                        ),
                        error=e.error,
                    )
                )
            else:
                self.results_viewer.clear_all_tables()
                self.results_viewer.data = {}
                relations = worker.result
                if relations:  # select query
                    self.relations = relations
                    if len(relations) < len(query_text.split(";")):
                        # mixed select and DDL statements
                        self.update_schema_data()
                elif bool(query_text.strip()):  # DDL/DML queries only
                    self.run_query_bar.set_responsive()
                    self.results_viewer.set_responsive()
                    self.results_viewer.show_table()
                    self.update_schema_data()
                else:  # blank query
                    self.run_query_bar.set_responsive()
                    self.results_viewer.set_responsive(did_run=False)
                    self.results_viewer.show_table()

    async def watch_relations(
        self, relations: Dict[str, duckdb.DuckDBPyRelation]
    ) -> None:
        """
        Only runs for select statements, except when first mounted.
        """
        # invalidate results so watch_data runs even if the results are the same
        self.results_viewer.clear_all_tables()
        self.set_result_viewer_data(relations)

    @work(
        exclusive=True,
        exit_on_error=True,
        group="duck_query_runners",
        description="fetching data from duckdb.",
    )
    async def set_result_viewer_data(
        self, relations: Dict[str, duckdb.DuckDBPyRelation]
    ) -> None:
        data: Dict[str, List[Tuple]] = {}
        for id_, rel in relations.items():
            self.results_viewer.push_table(table_id=id_, relation=rel)
            try:
                rel_data = rel.fetchall()
            except duckdb.DataError as e:
                self.push_screen(
                    ErrorModal(
                        title="DuckDB Error",
                        header=("DuckDB raised an error when running your query:"),
                        error=e,
                    )
                )
                self.results_viewer.show_table()
            else:
                data[id_] = rel_data
        self.results_viewer.data = data

    @work(
        exclusive=True,
        exit_on_error=False,
        group="duck_query_runners",
        description="Building relation.",
    )
    async def _build_relation(
        self, query_text: str
    ) -> Dict[str, duckdb.DuckDBPyRelation]:
        relations: Dict[str, duckdb.DuckDBPyRelation] = {}
        for q in query_text.split(";"):
            rel = self.connection.sql(q)
            if rel is not None:
                if self.run_query_bar.checkbox.value:
                    rel = rel.limit(self.limit)
                table_id = f"t{hash(rel)}"
                relations[table_id] = rel
        return relations

    @work(exclusive=True, group="duck_schema_updaters")
    async def update_schema_data(self) -> None:
        catalog = get_catalog(self.connection)
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.schema_viewer.update_tree(catalog)
