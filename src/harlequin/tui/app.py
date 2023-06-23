from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple, Type, Union

import duckdb
from textual import log, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.dom import DOMNode
from textual.driver import Driver
from textual.reactive import reactive
from textual.types import CSSPathType
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Footer, Input
from textual.worker import Worker, WorkerFailed, get_current_worker
from textual_textarea import TextArea

from harlequin.duck_ops import connect, get_catalog
from harlequin.exception import HarlequinExit
from harlequin.tui.components import (
    CodeEditor,
    ErrorModal,
    ResultsTable,
    ResultsViewer,
    RunQueryBar,
    SchemaViewer,
)
from harlequin.tui.utils import short_type


class Harlequin(App, inherit_bindings=False):
    """
    A Textual App for a SQL client for DuckDB.
    """

    CSS_PATH = "app.css"
    MAX_RESULTS = 50_000

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar", show=False),
        Binding("f9", "toggle_sidebar", "Toggle Sidebar", show=False),
        Binding("f10", "toggle_full_screen", "Toggle Full Screen Mode", show=False),
    ]

    query_text: reactive[str] = reactive(str)
    relation: reactive[Union[duckdb.DuckDBPyRelation, None]] = reactive(None)
    data: reactive[List[Tuple]] = reactive(list)
    full_screen: reactive[bool] = reactive(False)
    sidebar_hidden: reactive[bool] = reactive(False)

    def __init__(
        self,
        db_path: Sequence[Union[str, Path]],
        read_only: bool = False,
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
            self.connection = connect(db_path, read_only=read_only)
        except HarlequinExit:
            self.exit()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        with Horizontal():
            yield SchemaViewer("Data Catalog", connection=self.connection)
            with Vertical(id="main_panel"):
                yield CodeEditor(language="sql", theme=self.theme)
                yield RunQueryBar()
                yield ResultsViewer()
        yield Footer()

    async def on_mount(self) -> None:
        self.schema_viewer = self.query_one(SchemaViewer)
        self.editor = self.query_one(TextArea)
        self.results_viewer = self.query_one(ResultsViewer)
        self.run_query_bar = self.query_one(RunQueryBar)
        self.footer = self.query_one(Footer)

        self.set_focus(self.editor)
        self.run_query_bar.checkbox.value = False

        worker = self.update_schema_data()
        await worker.wait()

    def on_code_editor_submitted(self, message: CodeEditor.Submitted) -> None:
        message.stop()
        self.query_text = message.text

    def on_button_pressed(self, message: Button.Pressed) -> None:
        message.stop()
        self.query_text = self.editor.text

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
                message.input.tooltip = f"[red]Validation Error:[/red]\n{failures}"

    def on_input_submitted(self, message: Input.Submitted) -> None:
        if message.input.id == "limit_input":
            message.stop()
            if (
                message.input.value
                and message.validation_result
                and message.validation_result.is_valid
            ):
                self.query_text = self.editor.text

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

    def set_data(self, data: List[Tuple]) -> None:
        log(f"set_data {len(data)}")
        self.data = data

    def watch_full_screen(self, full_screen: bool) -> None:
        full_screen_widgets = [self.editor, self.results_viewer]
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
            if target == self.editor:
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

    async def watch_query_text(self, query_text: str) -> None:
        if query_text:
            self.full_screen = False
            pane = self.results_viewer
            self.run_query_bar.set_not_responsive()
            pane.show_loading()
            pane.set_not_responsive()
            try:
                worker = self._build_relation(query_text)
                await worker.wait()
            except WorkerFailed as e:
                self.run_query_bar.set_responsive()
                pane.set_responsive()
                pane.show_table()
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
                table = pane.get_table()
                table.clear(columns=True)
                relation = worker.result
                if relation:  # select query
                    self.relation = relation
                elif bool(query_text.strip()):  # DDL/DML query
                    self.run_query_bar.set_responsive()
                    pane.set_responsive()
                    pane.show_table()
                    self.data = []
                    self.update_schema_data()
                else:  # blank query
                    self.run_query_bar.set_responsive()
                    pane.set_responsive(did_run=False)
                    pane.show_table()
                    self.data = []

    async def watch_relation(
        self, relation: Union[duckdb.DuckDBPyRelation, None]
    ) -> None:
        """
        Only runs for select statements, except when first mounted.
        """
        # invalidate results so watch_data runs even if the results are the same
        self.data = []
        if relation is not None:
            table = self.results_viewer.get_table()
            short_types = [short_type(t) for t in relation.dtypes]
            table.add_columns(
                *[
                    f"{name} [#888888]{data_type}[/]"
                    for name, data_type in zip(relation.columns, short_types)
                ]
            )
            try:
                worker = self.fetch_relation_data(relation)
                await worker.wait()
            except WorkerFailed as e:
                self.push_screen(
                    ErrorModal(
                        title="DuckDB Error",
                        header=("DuckDB raised an error when running your query:"),
                        error=e.error,
                    )
                )
                self.results_viewer.show_table()

    async def watch_data(self, data: List[Tuple]) -> None:
        if data:
            pane = self.results_viewer
            pane.set_not_responsive(max_rows=self.MAX_RESULTS, total_rows=len(data))
            table = pane.get_table()
            for i, chunk in self.chunk(data[: self.MAX_RESULTS]):
                worker = self.add_data_to_table(table, chunk)
                await worker.wait()
                pane.increment_progress_bar()
                if i == 0:
                    pane.show_table()
            pane.set_responsive(max_rows=self.MAX_RESULTS, total_rows=len(data))
            self.run_query_bar.set_responsive()
            table.focus()

    @staticmethod
    def chunk(
        data: List[Tuple], chunksize: int = 2000
    ) -> Iterator[Tuple[int, List[Tuple]]]:
        log(f"chunk {len(data)}")
        for i in range(len(data) // chunksize + 1):
            log(f"yielding chunk {i}")
            yield i, data[i * chunksize : (i + 1) * chunksize]

    @work(exclusive=True, exit_on_error=False)  # type: ignore
    def _build_relation(self, query_text: str) -> Union[duckdb.DuckDBPyRelation, None]:
        relation = self.connection.sql(query_text)
        if relation and self.run_query_bar.checkbox.value:
            relation = relation.limit(self.limit)
        return relation

    @work(exclusive=True, exit_on_error=False)  # type: ignore
    def fetch_relation_data(self, relation: duckdb.DuckDBPyRelation) -> None:
        log(f"fetch_relation_data {hash(relation)}")
        data = relation.fetchall()
        log(f"fetch_relation_data FINISHED {hash(relation)}")
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.call_from_thread(self.set_data, data)

    @work(exclusive=False)
    def add_data_to_table(self, table: ResultsTable, data: List[Tuple]) -> Worker:
        log(f"add_data_to_table {len(data)}")
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.call_from_thread(table.add_rows, data)
        return worker

    @work(exclusive=True)
    def update_schema_data(self) -> None:
        catalog = get_catalog(self.connection)
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.call_from_thread(self.schema_viewer.update_tree, catalog)


if __name__ == "__main__":
    app = Harlequin(["f1.db"])
    app.run()
