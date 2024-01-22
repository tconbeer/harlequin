from __future__ import annotations

import asyncio
import os
import time
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional, Type, Union

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.stylesheet import Stylesheet
from textual.dom import DOMNode
from textual.driver import Driver
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen, ScreenResultCallbackType, ScreenResultType
from textual.types import CSSPathType
from textual.widget import AwaitMount, Widget
from textual.widgets import Button, Footer, Input
from textual.worker import Worker, WorkerState
from textual_fastdatatable import DataTable
from textual_fastdatatable.backend import AutoBackendType

from harlequin import HarlequinConnection
from harlequin.adapter import HarlequinAdapter, HarlequinCursor
from harlequin.autocomplete import completer_factory
from harlequin.catalog import Catalog, NewCatalog
from harlequin.catalog_cache import (
    CatalogCache,
    get_catalog_cache,
    update_catalog_cache,
)
from harlequin.colors import HarlequinColors
from harlequin.components import (
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
from harlequin.editor_cache import BufferState, Cache
from harlequin.editor_cache import write_cache as write_editor_cache
from harlequin.exception import (
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinError,
    HarlequinQueryError,
    HarlequinThemeError,
    pretty_error_message,
    pretty_print_error,
)


class CatalogCacheLoaded(Message):
    def __init__(self, cache: CatalogCache) -> None:
        super().__init__()
        self.cache = cache


class DatabaseConnected(Message):
    def __init__(self, connection: HarlequinConnection) -> None:
        super().__init__()
        self.connection = connection


class QuerySubmitted(Message):
    def __init__(self, query_text: str, limit: int | None) -> None:
        super().__init__()
        self.query_text = query_text.strip()
        self.limit = limit
        self.submitted_at = time.monotonic()


class QueriesExecuted(Message):
    def __init__(
        self, query_count: int, cursors: Dict[str, HarlequinCursor], submitted_at: float
    ) -> None:
        super().__init__()
        self.query_count = query_count
        self.cursors = cursors
        self.submitted_at = submitted_at


class ResultsFetched(Message):
    def __init__(
        self,
        cursors: Dict[str, HarlequinCursor],
        data: Dict[str, tuple[list[tuple[str, str]], AutoBackendType | None]],
        errors: List[BaseException],
        elapsed: float,
    ) -> None:
        super().__init__()
        self.cursors = cursors
        self.data = data
        self.errors = errors
        self.elapsed = elapsed


class Harlequin(App, inherit_bindings=False):
    """
    The SQL IDE for your Terminal.
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
        Binding("ctrl+r", "refresh_catalog", "Refresh Data Catalog", show=False),
    ]

    full_screen: reactive[bool] = reactive(False)
    sidebar_hidden: reactive[bool] = reactive(False)

    def __init__(
        self,
        adapter: HarlequinAdapter,
        *,
        connection_hash: str | None = None,
        theme: str = "harlequin",
        show_files: Path | None = None,
        show_s3: str | None = None,
        max_results: int | str = 100_000,
        driver_class: Union[Type[Driver], None] = None,
        css_path: Union[CSSPathType, None] = None,
        watch_css: bool = False,
    ):
        super().__init__(driver_class, css_path, watch_css)
        self.adapter = adapter
        self.connection_hash = connection_hash
        self.catalog: Catalog | None = None
        self.theme = theme
        self.show_files = show_files
        self.show_s3 = show_s3 or None
        try:
            self.max_results = int(max_results)
        except ValueError:
            pretty_print_error(
                HarlequinConfigError(
                    f"limit={max_results!r} was set by config file but is not "
                    "a valid integer."
                )
            )
            self.exit(return_code=2)
        self.query_timer: Union[float, None] = None
        self.connection: HarlequinConnection | None = None

        try:
            self.app_colors = HarlequinColors.from_theme(theme)
        except HarlequinThemeError as e:
            pretty_print_error(e)
            self.exit(return_code=2)
        else:
            self.design = self.app_colors.design_system
            self.stylesheet = Stylesheet(variables=self.get_css_variables())

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        with Horizontal():
            yield DataCatalog(
                type_color=self.app_colors.gray,
                show_files=self.show_files,
                show_s3=self.show_s3,
            )
            with Vertical(id="main_panel"):
                yield EditorCollection(language="sql", theme=self.theme)
                yield RunQueryBar(
                    max_results=self.max_results, classes="non-responsive"
                )
                yield ResultsViewer(
                    max_results=self.max_results,
                    type_color=self.app_colors.gray,
                )
        yield Footer()

    def push_screen(  # type: ignore
        self,
        screen: Union[Screen[ScreenResultType], str],
        callback: Union[ScreenResultCallbackType[ScreenResultType], None] = None,
        wait_for_dismiss: bool = False,
    ) -> Union[AwaitMount, asyncio.Future[ScreenResultType]]:
        if self.editor._has_focus_within:
            self.editor.text_input._pause_blink(visible=True)
        return super().push_screen(  # type: ignore
            screen,
            callback=callback,
            wait_for_dismiss=wait_for_dismiss,
        )

    def pop_screen(self) -> Screen[object]:
        new_screen = super().pop_screen()
        if len(self.screen_stack) == 1 and self.editor._has_focus_within:
            self.editor.text_input._restart_blink()
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

        self._connect()
        self._load_catalog_cache()

    @on(Button.Pressed, "#run_query")
    def submit_query_from_run_query_bar(self, message: Button.Pressed) -> None:
        message.stop()
        self.post_message(
            QuerySubmitted(
                query_text=self._get_query_text(),
                limit=self.run_query_bar.limit_value,
            )
        )

    @on(CatalogCacheLoaded)
    def build_trees(self, message: CatalogCacheLoaded) -> None:
        if self.connection_hash is not None and (
            cached_db := message.cache.get_db(self.connection_hash)
        ):
            self.post_message(NewCatalog(catalog=cached_db))
        if self.show_s3 is not None:
            self.data_catalog.load_s3_tree_from_cache(message.cache)

    @on(CodeEditor.Submitted)
    def submit_query_from_editor(self, message: CodeEditor.Submitted) -> None:
        message.stop()
        self.post_message(
            QuerySubmitted(
                query_text=self._get_query_text(), limit=self.run_query_bar.limit_value
            )
        )

    @on(DatabaseConnected)
    def initialize_app(self, message: DatabaseConnected) -> None:
        self.connection = message.connection
        self.run_query_bar.set_responsive()
        self.results_viewer.show_table(did_run=False)
        if message.connection.init_message:
            self.notify(message.connection.init_message, title="Database Connected.")
        else:
            self.notify("Database Connected.")
        self.update_schema_data()

    @on(DataCatalog.NodeSubmitted)
    def insert_node_into_editor(self, message: DataCatalog.NodeSubmitted) -> None:
        message.stop()
        self.editor.insert_text_at_selection(text=message.insert_name)
        self.editor.focus()

    @on(DataCatalog.NodeCopied)
    def copy_node_name(self, message: DataCatalog.NodeCopied) -> None:
        message.stop()
        self.editor.text_input.clipboard = message.copy_name
        if self.editor.use_system_clipboard:
            try:
                self.editor.text_input.system_copy(message.copy_name)
            except Exception:
                self.notify("Error copying data to system clipboard.", severity="error")
            else:
                self.notify("Selected label copied to clipboard.")

    @on(EditorCollection.EditorSwitched)
    def update_internal_editor_state(
        self, message: EditorCollection.EditorSwitched
    ) -> None:
        if message.active_editor is not None:
            self.editor = message.active_editor
        else:
            self.editor = self.editor_collection.current_editor

    def on_text_area_selection_changed(self) -> None:
        if self._validate_selection():
            self.run_query_bar.button.label = "Run Selection"
        else:
            self.run_query_bar.button.label = "Run Query"

    @on(Input.Changed, "#limit_input")
    def update_limit_tooltip(self, message: Input.Changed) -> None:
        message.stop()
        if (
            message.input.value
            and message.validation_result
            and message.validation_result.is_valid
        ):
            message.input.tooltip = None
        elif message.validation_result:
            failures = "\n".join(message.validation_result.failure_descriptions)
            message.input.tooltip = (
                f"[{self.app_colors.error}]Validation Error:[/]\n{failures}"
            )

    @on(Input.Submitted, "#limit_input")
    def submit_query_if_limit_valid(self, message: Input.Submitted) -> None:
        message.stop()
        if (
            message.input.value
            and message.validation_result
            and message.validation_result.is_valid
        ):
            self.post_message(
                QuerySubmitted(
                    query_text=self._get_query_text(),
                    limit=self.run_query_bar.limit_value,
                )
            )

    @on(DataTable.SelectionCopied)
    def copy_data_to_clipboard(self, message: DataTable.SelectionCopied) -> None:
        message.stop()
        # Excel, sheets, and Snowsight all use a TSV format for copying tabular data
        text = os.linesep.join("\t".join(map(str, row)) for row in message.values)
        self.editor.text_input.clipboard = text
        if self.editor.use_system_clipboard:
            try:
                self.editor.text_input.system_copy(text)
            except Exception:
                self.notify("Error copying data to system clipboard.", severity="error")
            else:
                self.notify("Selected data copied to clipboard.")

    @on(Worker.StateChanged)
    async def handle_worker_error(self, message: Worker.StateChanged) -> None:
        if message.state == WorkerState.ERROR:
            await self._handle_worker_error(message)

    async def _handle_worker_error(self, message: Worker.StateChanged) -> None:
        if (
            message.worker.name == "update_schema_data"
            and message.worker.error is not None
        ):
            self._push_error_modal(
                title="Catalog Error",
                header="Could not update data catalog",
                error=message.worker.error,
            )
            self.data_catalog.database_tree.loading = False
        elif (
            message.worker.name == "_execute_query" and message.worker.error is not None
        ):
            self.run_query_bar.set_responsive()
            self.results_viewer.show_table()
            header = getattr(
                message.worker.error, "title", message.worker.error.__class__.__name__
            )
            self._push_error_modal(
                title="Query Error",
                header=header,
                error=message.worker.error,
            )
        elif message.worker.name == "_connect" and message.worker.error is not None:
            title = getattr(
                message.worker.error,
                "title",
                "Harlequin could not connect to your database.",
            )
            error = (
                message.worker.error
                if isinstance(message.worker.error, HarlequinError)
                else HarlequinConnectionError(
                    msg=str(message.worker.error), title=title
                )
            )
            self.exit(return_code=2, message=pretty_error_message(error))

    @on(DataCatalog.CatalogError)
    def handle_catalog_error(self, message: DataCatalog.CatalogError) -> None:
        self._push_error_modal(
            title=f"Catalog Error: {message.catalog_type}",
            header=f"Could not populate the {message.catalog_type} data catalog",
            error=message.error,
        )

    @on(NewCatalog)
    def update_tree_and_completers(self, message: NewCatalog) -> None:
        self.catalog = message.catalog
        self.data_catalog.update_database_tree(message.catalog)
        self.update_completers(message.catalog)

    @on(QueriesExecuted)
    def fetch_data_or_reset_table(self, message: QueriesExecuted) -> None:
        if message.cursors:  # select query
            self._fetch_data(message.cursors, message.submitted_at)
        else:
            self.run_query_bar.set_responsive()
            self.results_viewer.show_table(did_run=message.query_count > 0)
        if (n := message.query_count - len(message.cursors)) > 0:
            # at least one DDL statement
            elapsed = time.monotonic() - message.submitted_at
            self.notify(
                f"{n} DDL/DML {'query' if n == 1 else 'queries'} "
                f"executed successfully in {elapsed:.2f} seconds."
            )
            self.update_schema_data()

    @on(ResultsFetched)
    async def load_tables(self, message: ResultsFetched) -> None:
        for id_, (cols, data) in message.data.items():
            await self.results_viewer.push_table(
                table_id=id_,
                column_labels=cols,
                data=data,  # type: ignore
            )
        if message.errors:
            header = getattr(
                message.errors[0],
                "title",
                "The database raised an error when running your query:",
            )
            self._push_error_modal(
                title="Query Error",
                header=header,
                error=message.errors[0],
            )
        else:
            self.notify(
                f"{len(message.cursors)} "
                f"{'query' if len(message.cursors) == 1 else 'queries'} "
                f"executed successfully in {message.elapsed:.2f} seconds."
            )
        self.run_query_bar.set_responsive()
        if len(message.errors) == len(message.cursors):
            self.results_viewer.show_table(did_run=False)
        else:
            self.results_viewer.show_table(did_run=True)
            self.results_viewer.focus()

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

    @on(QuerySubmitted)
    def execute_query(self, message: QuerySubmitted) -> None:
        if self.connection is None:
            return
        if message.query_text:
            self.full_screen = False
            self.run_query_bar.set_not_responsive()
            self.results_viewer.show_loading()
            self._execute_query(message)

    def watch_sidebar_hidden(self, sidebar_hidden: bool) -> None:
        if sidebar_hidden:
            if self.data_catalog.has_focus:
                self.editor.focus()
        self.data_catalog.disabled = sidebar_hidden

    def action_export(self) -> None:
        if not self.adapter.implements_copy:
            self._push_error_modal(
                title="Not Implemented by Adapter",
                header="Harlequin could not load the Export menu.",
                error=NotImplementedError(
                    "The current adapter has not implemented the export menu. "
                    "Please write a COPY statement using the query editor."
                ),
            )
            return
        show_export_error = partial(
            self._push_error_modal,
            "Export Data Error",
            "Could not export data.",
        )
        queries = self._split_query_text(self._get_query_text())
        if not queries or not queries[0]:
            show_export_error(
                error=ValueError(
                    "You must type a query into the editor first, or move your "
                    "cursor into an existing query."
                )
            )
            return
        notify = partial(self.notify, "Data exported successfully.")
        callback = partial(
            export_callback,
            query=queries[0],
            connection=self.connection,
            success_callback=notify,
            error_callback=show_export_error,
        )
        self.app.push_screen(
            ExportScreen(adapter=self.adapter, id="export_screen"), callback
        )

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
        write_editor_cache(Cache(focus_index=focus_index, buffers=buffers))
        update_catalog_cache(
            self.connection_hash, self.catalog, self.data_catalog.s3_tree
        )
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

    def action_refresh_catalog(self) -> None:
        self.data_catalog.database_tree.loading = True
        self.update_schema_data()
        self.data_catalog.update_file_tree()
        self.data_catalog.update_s3_tree()

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="connect",
        description="Connecting to DB",
    )
    def _connect(self) -> None:
        connection = self.adapter.connect()
        self.post_message(DatabaseConnected(connection=connection))

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="cache_loaders",
        description="Loading cached catalog",
    )
    def _load_catalog_cache(self) -> None:
        cache = get_catalog_cache()
        if cache is not None:
            self.post_message(CatalogCacheLoaded(cache=cache))

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="query_runners",
        description="Executing queries.",
    )
    def _execute_query(self, message: QuerySubmitted) -> None:
        if self.connection is None:
            return
        cursors: Dict[str, HarlequinCursor] = {}
        queries = self._split_query_text(message.query_text)
        for q in queries:
            cur = self.connection.execute(q)
            if cur is not None:
                if message.limit is not None:
                    cur = cur.set_limit(message.limit)
                table_id = f"t{hash(cur)}"
                cursors[table_id] = cur
        self.post_message(
            QueriesExecuted(
                query_count=len(queries),
                cursors=cursors,
                submitted_at=message.submitted_at,
            )
        )

    def _get_query_text(self) -> str:
        return (
            self._validate_selection()
            or self.editor.current_query
            or self.editor.previous_query
        )

    @staticmethod
    def _split_query_text(query_text: str) -> List[str]:
        return [q for q in query_text.split(";") if q.strip()]

    def _push_error_modal(self, title: str, header: str, error: BaseException) -> None:
        self.push_screen(
            ErrorModal(
                title=title,
                header=header,
                error=error,
            ),
        )

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=True,
        group="query_runners",
        description="fetching data from adapter.",
    )
    def _fetch_data(
        self, cursors: Dict[str, HarlequinCursor], submitted_at: float
    ) -> None:
        errors: List[BaseException] = []
        data: Dict[str, tuple[list[tuple[str, str]], AutoBackendType | None]] = {}
        for id_, cur in cursors.items():
            try:
                cur_data = cur.fetchall()
            except HarlequinQueryError as e:
                errors.append(e)
            else:
                data[id_] = (cur.columns(), cur_data)
        elapsed = time.monotonic() - submitted_at
        self.post_message(
            ResultsFetched(cursors=cursors, data=data, errors=errors, elapsed=elapsed)
        )

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=True,
        group="completer_builders",
        description="building completers",
    )
    def update_completers(self, catalog: Catalog) -> None:
        if self.connection is None:
            return
        if (
            self.editor_collection.word_completer is not None
            and self.editor_collection.member_completer is not None
        ):
            self.editor_collection.word_completer.update_catalog(catalog=catalog)
            self.editor_collection.member_completer.update_catalog(catalog=catalog)
        else:
            extra_completions = self.connection.get_completions()
            word_completer, member_completer = completer_factory(
                catalog=catalog,
                extra_completions=extra_completions,
                type_color=self.app_colors.gray,
            )
            self.editor_collection.word_completer = word_completer
            self.editor_collection.member_completer = member_completer

    @work(thread=True, exclusive=True, exit_on_error=False, group="schema_updaters")
    def update_schema_data(self) -> None:
        if self.connection is None:
            return
        catalog = self.connection.get_catalog()
        self.post_message(NewCatalog(catalog=catalog))

    def _validate_selection(self) -> str:
        """
        If the selection is valid query, return it. Otherwise
        return the empty string.
        """
        selection = self.editor.selected_text
        if self.connection is None:
            return selection
        if selection:
            try:
                return self.connection.validate_sql(selection)
            except NotImplementedError:
                return selection
        else:
            return ""
