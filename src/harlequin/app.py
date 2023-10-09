import json
import time
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Type, Union

import duckdb
import pyarrow as pa
from rich import print
from rich.panel import Panel
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.stylesheet import Stylesheet
from textual.dom import DOMNode
from textual.driver import Driver
from textual.reactive import reactive
from textual.screen import Screen, ScreenResultCallbackType, ScreenResultType
from textual.types import CSSPathType
from textual.widget import AwaitMount, Widget
from textual.widgets import Button, Checkbox, Footer, Input
from textual.worker import WorkerFailed, get_current_worker

from harlequin.cache import BufferState, Cache, write_cache
from harlequin.colors import HarlequinColors
from harlequin.components import (
    CatalogItem,
    CodeEditor,
    DataCatalog,
    EditorCollection,
    ErrorModal,
    ExportScreen,
    HelpScreen,
    ResultsViewer,
    RunQueryBar,
    export_callback,
)
from harlequin.duck_ops import connect, get_catalog, get_column_labels_for_relation
from harlequin.exception import (
    HarlequinConnectionError,
    HarlequinThemeError,
)


class Harlequin(App, inherit_bindings=False):
    """
    A Textual App for a SQL client for DuckDB.
    """

    CSS_PATH = "global.tcss"

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
        theme: str = "monokai",
        init_script: Tuple[Path, str] = (Path(), ""),
        max_results: int = 100_000,
        read_only: bool = False,
        allow_unsigned_extensions: bool = False,
        extensions: Union[List[str], None] = None,
        force_install_extensions: bool = False,
        custom_extension_repo: Union[str, None] = None,
        md_token: Union[str, None] = None,
        md_saas: bool = False,
        driver_class: Union[Type[Driver], None] = None,
        css_path: Union[CSSPathType, None] = None,
        watch_css: bool = False,
    ):
        super().__init__(driver_class, css_path, watch_css)
        self.theme = theme
        self.max_results = max_results
        self.limit = min(500, max_results) if max_results > 0 else 500
        self.query_timer: Union[float, None] = None
        try:
            self.connection = connect(
                db_path,
                init_script=init_script,
                read_only=read_only,
                allow_unsigned_extensions=allow_unsigned_extensions,
                extensions=extensions,
                force_install_extensions=force_install_extensions,
                custom_extension_repo=custom_extension_repo,
                md_token=md_token,
                md_saas=md_saas,
            )
        except HarlequinConnectionError as e:
            print(
                Panel.fit(
                    str(e),
                    title=e.title
                    if e.title
                    else (
                        "Harlequin encountered an error "
                        "while connecting to the database."
                    ),
                    title_align="left",
                    border_style="red",
                )
            )
            self.exit()
        else:
            if init_script[1]:
                self.notify(f"Executed commands from {init_script[0]}")

        try:
            self.app_colors = HarlequinColors.from_theme(theme)
        except HarlequinThemeError as e:
            print(
                Panel.fit(
                    (
                        f"No theme found with the name {e}.\n"
                        "Theme must be the name of a Pygments Style. "
                        "You can browse the supported styles here:\n"
                        "https://pygments.org/styles/"
                    ),
                    title="Harlequin couldn't load your theme.",
                    title_align="left",
                    border_style="red",
                )
            )
            self.exit()
        else:
            self.design = self.app_colors.design_system
            self.stylesheet = Stylesheet(variables=self.get_css_variables())

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        with Horizontal():
            yield DataCatalog(
                connection=self.connection, type_color=self.app_colors.gray
            )
            with Vertical(id="main_panel"):
                yield EditorCollection(language="sql", theme=self.theme)
                yield RunQueryBar(max_results=self.max_results)
                yield ResultsViewer(
                    max_results=self.max_results, type_color=self.app_colors.gray
                )
        yield Footer()

    def push_screen(
        self,
        screen: Union[Screen[ScreenResultType], str],
        callback: Union[ScreenResultCallbackType[ScreenResultType], None] = None,
    ) -> AwaitMount:
        if self.editor._has_focus_within:
            self.editor.text_input.blink_timer.pause()
        return super().push_screen(screen, callback=callback)

    def pop_screen(self) -> Screen[object]:
        new_screen = super().pop_screen()
        if len(self.screen_stack) == 1 and self.editor._has_focus_within:
            self.editor.text_input.blink_timer.resume()
        return new_screen

    async def on_mount(self) -> None:
        self.data_catalog = self.query_one(DataCatalog)
        self.editor_collection = self.query_one(EditorCollection)
        self.editor = self.editor_collection.current_editor
        self.results_viewer = self.query_one(ResultsViewer)
        self.run_query_bar = self.query_one(RunQueryBar)
        self.footer = self.query_one(Footer)

        self.editor.focus()
        self.run_query_bar.checkbox.value = False

        worker = self._update_schema_data()
        await worker.wait()

    def on_button_pressed(self, message: Button.Pressed) -> None:
        message.stop()
        if message.control.id == "run_query":
            self._set_query_text()

    def on_code_editor_submitted(self, message: CodeEditor.Submitted) -> None:
        message.stop()
        self._set_query_text()

    def on_data_catalog_node_submitted(
        self, message: DataCatalog.NodeSubmitted[CatalogItem]
    ) -> None:
        message.stop()
        if message.node.data:
            self.editor.insert_text_at_selection(text=message.node.data.query_name)
            self.editor.focus()

    def on_editor_collection_editor_switched(
        self, message: EditorCollection.EditorSwitched
    ) -> None:
        if message.active_editor is not None:
            self.editor = message.active_editor
        else:
            self.editor = self.editor_collection.current_editor

    def on_text_area_cursor_moved(self) -> None:
        self.selection_text = self._validate_selection()

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
            self.data_catalog.disabled = True
        else:
            for w in all_widgets:
                w.disabled = False
            self.data_catalog.disabled = self.sidebar_hidden

    async def watch_query_text(self, query_text: str) -> None:
        if query_text:
            self.query_timer = time.monotonic()
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
                self._push_error_modal(
                    title="DuckDB Error",
                    header=(
                        "DuckDB raised an error when compiling "
                        "or running your query:"
                    ),
                    error=e.error,
                )
            else:
                self.results_viewer.clear_all_tables()
                self.results_viewer.data = {}
                relations = worker.result
                number_of_queries = len(query_text.split(";"))
                elapsed = time.monotonic() - self.query_timer
                if relations:  # select query
                    self.relations = relations
                    if len(relations) < number_of_queries:
                        # mixed select and DDL statements
                        n = number_of_queries - len(relations)
                        self.notify(
                            f"{n} DDL/DML {'query' if n == 1 else 'queries'} "
                            f"executed successfully in {elapsed:.2f} seconds."
                        )
                        self._update_schema_data()
                elif bool(query_text.strip()):  # DDL/DML queries only
                    self.run_query_bar.set_responsive()
                    self.results_viewer.set_responsive()
                    self.results_viewer.show_table()
                    self.notify(
                        f"{number_of_queries} DDL/DML "
                        f"{'query' if number_of_queries == 1 else 'queries'} "
                        f"executed successfully in {elapsed:.2f} seconds."
                    )
                    self.query_timer = None
                    self._update_schema_data()
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
        self._set_result_viewer_data(relations)

    def watch_selection_text(self, selection_text: str) -> None:
        if selection_text:
            self.run_query_bar.button.label = "Run Selection"
        else:
            self.run_query_bar.button.label = "Run Query"

    def watch_sidebar_hidden(self, sidebar_hidden: bool) -> None:
        if sidebar_hidden:
            if self.data_catalog.has_focus:
                self.editor.focus()
        self.data_catalog.disabled = sidebar_hidden

    def action_export(self) -> None:
        show_export_error = partial(
            self._push_error_modal,
            "Export Data Error",
            "Could not export data.",
        )
        active_table = self.results_viewer.get_visible_table()
        if (
            not self.relations
            or active_table is None
            or active_table.id is None
            or active_table.id not in self.relations
        ):
            show_export_error(
                error=ValueError("There is no data to export. Run the query first.")
            )
            return
        relation = self.relations[active_table.id]
        notify = partial(self.notify, "Data exported successfully.")
        callback = partial(
            export_callback,
            relation=relation,
            connection=self.connection,
            success_callback=notify,
            error_callback=show_export_error,
        )
        self.app.push_screen(ExportScreen(id="export_screen"), callback)

    def action_focus_data_catalog(self) -> None:
        if self.sidebar_hidden or self.data_catalog.disabled:
            self.action_toggle_sidebar()
        self.data_catalog.focus()

    def action_focus_query_editor(self) -> None:
        self.editor.focus()

    def action_focus_results_viewer(self) -> None:
        self.results_viewer.focus()

    async def action_quit(self) -> None:
        buffers = []
        for i, editor in enumerate(self.editor_collection.all_editors):
            if editor == self.editor_collection.current_editor:
                focus_index = i
            buffers.append(
                BufferState(editor.cursor, editor.selection_anchor, editor.text)
            )
        write_cache(Cache(focus_index=focus_index, buffers=buffers))
        await super().action_quit()

    def action_show_help_screen(self) -> None:
        self.push_screen(HelpScreen(id="help_screen"))

    def action_toggle_full_screen(self) -> None:
        self.full_screen = not self.full_screen

    def action_toggle_sidebar(self) -> None:
        """
        sidebar_hidden and self.sidebar.disabled both hold important state.
        The sidebar can be hidden with either ctrl+b or f10, and we need
        to persist the state depending on how that happens
        """
        if self.sidebar_hidden is False and self.data_catalog.disabled is True:
            # sidebar was hidden by f10; toggle should show it
            self.data_catalog.disabled = False
        else:
            self.sidebar_hidden = not self.sidebar_hidden

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

    def _set_query_text(self) -> None:
        self.query_text = self._validate_selection() or self.editor.current_query

    def _push_error_modal(self, title: str, header: str, error: BaseException) -> None:
        self.push_screen(
            ErrorModal(
                title=title,
                header=header,
                error=error,
            )
        )

    @work(
        exclusive=True,
        exit_on_error=True,
        group="duck_query_runners",
        description="fetching data from duckdb.",
    )
    async def _set_result_viewer_data(
        self, relations: Dict[str, duckdb.DuckDBPyRelation]
    ) -> None:
        data: Dict[str, pa.Table] = {}
        errors: List[BaseException] = []
        for id_, rel in relations.items():
            try:
                rel_data: pa.Table = rel.fetch_arrow_table()
            except (duckdb.DataError, duckdb.InternalException) as e:
                errors.append(e)
            else:
                self.results_viewer.push_table(
                    table_id=id_,
                    column_labels=get_column_labels_for_relation(rel),  # type: ignore
                    data=rel_data.slice(0, self.max_results)
                    if self.max_results > 0
                    else rel_data,
                )
                data[id_] = rel_data
        if errors:
            self._push_error_modal(
                title="DuckDB Error",
                header=("DuckDB raised an error when running your query:"),
                error=errors[0],
            )
        elif self.query_timer is not None:
            elapsed = time.monotonic() - self.query_timer
            self.notify(
                f"{len(relations)} {'query' if len(relations) == 1 else 'queries'} "
                f"executed successfully in {elapsed:.2f} seconds."
            )
        self.run_query_bar.set_responsive()
        self.results_viewer.show_table()
        self.results_viewer.data = data
        if not data:
            self.results_viewer.set_responsive(did_run=len(errors) == len(relations))
        else:
            self.results_viewer.set_responsive(data=data, did_run=True)
            self.results_viewer.focus()

    @work(exclusive=True, group="duck_schema_updaters")
    async def _update_schema_data(self) -> None:
        catalog = get_catalog(self.connection)
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.data_catalog.update_tree(catalog)

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
