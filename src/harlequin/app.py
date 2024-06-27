from __future__ import annotations

import asyncio
import os
import sys
import time
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Type, Union

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import DOMQuery
from textual.dom import DOMNode
from textual.driver import Driver
from textual.lazy import Lazy
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
from harlequin.actions import HARLEQUIN_ACTIONS
from harlequin.adapter import HarlequinAdapter, HarlequinCursor
from harlequin.app_base import AppBase
from harlequin.autocomplete import completer_factory
from harlequin.bindings import bind
from harlequin.catalog import Catalog, NewCatalog
from harlequin.catalog_cache import (
    CatalogCache,
    get_catalog_cache,
    update_catalog_cache,
)
from harlequin.components import (
    CodeEditor,
    DataCatalog,
    EditorCollection,
    ErrorModal,
    ExportScreen,
    HelpScreen,
    HistoryScreen,
    ResultsViewer,
    RunQueryBar,
    export_callback,
)
from harlequin.copy_formats import HARLEQUIN_COPY_FORMATS, WINDOWS_COPY_FORMATS
from harlequin.editor_cache import BufferState, Cache
from harlequin.editor_cache import write_cache as write_editor_cache
from harlequin.exception import (
    HarlequinBindingError,
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinError,
    HarlequinQueryError,
    pretty_error_message,
    pretty_print_error,
)
from harlequin.history import History
from harlequin.messages import WidgetMounted
from harlequin.plugins import load_keymap_plugins
from harlequin.transaction_mode import HarlequinTransactionMode

if TYPE_CHECKING:
    from harlequin.keymap import HarlequinKeyMap


class CatalogCacheLoaded(Message):
    def __init__(self, cache: CatalogCache) -> None:
        super().__init__()
        self.cache = cache


class DatabaseConnected(Message):
    def __init__(self, connection: HarlequinConnection) -> None:
        super().__init__()
        self.connection = connection


class QueryError(Message):
    def __init__(self, query_text: str, error: BaseException) -> None:
        super().__init__()
        self.query_text = query_text
        self.error = error


class QuerySubmitted(Message):
    def __init__(self, query_text: str, limit: int | None) -> None:
        super().__init__()
        self.query_text = query_text.strip()
        self.limit = limit
        self.submitted_at = time.monotonic()


class QueriesExecuted(Message):
    def __init__(
        self,
        query_count: int,
        cursors: Dict[str, tuple[HarlequinCursor, str]],
        submitted_at: float,
        ddl_queries: list[str],
    ) -> None:
        super().__init__()
        self.query_count = query_count
        self.cursors = cursors
        self.submitted_at = submitted_at
        self.ddl_queries = ddl_queries


class ResultsFetched(Message):
    def __init__(
        self,
        cursors: Dict[str, tuple[HarlequinCursor, str]],
        data: Dict[str, tuple[list[tuple[str, str]], AutoBackendType | None, str]],
        errors: list[tuple[BaseException, str]],
        elapsed: float,
    ) -> None:
        super().__init__()
        self.cursors = cursors
        self.data = data
        self.errors = errors
        self.elapsed = elapsed


class TransactionModeChanged(Message):
    def __init__(self, new_mode: HarlequinTransactionMode | None) -> None:
        super().__init__()
        self.new_mode = new_mode


class Harlequin(AppBase):
    """
    The SQL IDE for your Terminal.
    """

    CSS_PATH = ["global.tcss", "app.tcss"]

    full_screen: reactive[bool] = reactive(False)
    sidebar_hidden: reactive[bool] = reactive(False)

    def __init__(
        self,
        adapter: HarlequinAdapter,
        *,
        keymap_names: Sequence[str] | None = None,
        user_defined_keymaps: Sequence[HarlequinKeyMap] | None = None,
        connection_hash: str | None = None,
        theme: str = "harlequin",
        show_files: Path | None = None,
        show_s3: str | None = None,
        max_results: int | str = 100_000,
        driver_class: Union[Type[Driver], None] = None,
        css_path: Union[CSSPathType, None] = None,
        watch_css: bool = False,
    ):
        super().__init__(
            theme=theme,
            driver_class=driver_class,
            css_path=css_path,
            watch_css=watch_css,
        )
        self.adapter = adapter
        self.connection_hash = connection_hash
        self.catalog: Catalog | None = None
        self.history: History | None = None
        self.theme = theme
        self.show_files = show_files
        self.show_s3 = show_s3 or None
        try:
            self.max_results = int(max_results)
        except ValueError:
            self.exit(
                return_code=2,
                message=pretty_error_message(
                    HarlequinConfigError(
                        f"limit={max_results!r} was set by config file but is not "
                        "a valid integer."
                    )
                ),
            )
        self.query_timer: Union[float, None] = None
        self.connection: HarlequinConnection | None = None

        if keymap_names is None:
            keymap_names = ("vscode",)
        if user_defined_keymaps is None:
            user_defined_keymaps = []

        self.keymap_names = keymap_names
        try:
            self.all_keymaps = load_keymap_plugins(
                user_defined_keymaps=user_defined_keymaps
            )
        except HarlequinConfigError as e:
            self.exit(return_code=2, message=pretty_error_message(e))

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        self.data_catalog = DataCatalog(
            type_color=self.app_colors.gray,
            show_files=self.show_files,
            show_s3=self.show_s3,
        )
        self.editor_collection = EditorCollection(
            language="sql", theme=self.theme, classes="hide-tabs"
        )
        self.editor: CodeEditor | None = None
        editor_placeholder = Lazy(widget=self.editor_collection)
        editor_placeholder.border_title = self.editor_collection.border_title
        editor_placeholder.loading = True
        self.results_viewer = ResultsViewer(
            max_results=self.max_results,
            type_color=self.app_colors.gray,
        )
        self.run_query_bar = RunQueryBar(
            max_results=self.max_results, classes="non-responsive"
        )
        self.footer = Footer()

        # lay out the widgets
        with Horizontal():
            yield self.data_catalog
            with Vertical(id="main_panel"):
                yield editor_placeholder
                yield self.run_query_bar
                yield self.results_viewer
        yield self.footer

    def push_screen(  # type: ignore
        self,
        screen: Union[Screen[ScreenResultType], str],
        callback: Union[ScreenResultCallbackType[ScreenResultType], None] = None,
        wait_for_dismiss: bool = False,
    ) -> Union[AwaitMount, asyncio.Future[ScreenResultType]]:
        if self.editor is not None and self.editor._has_focus_within:
            self.editor.text_input._pause_blink(visible=True)
        return super().push_screen(  # type: ignore
            screen,
            callback=callback,
            wait_for_dismiss=wait_for_dismiss,
        )

    def pop_screen(self) -> Screen[object]:
        new_screen = super().pop_screen()
        if (
            len(self.screen_stack) == 1
            and self.editor is not None
            and self.editor._has_focus_within
        ):
            self.editor.text_input._restart_blink()
        return new_screen

    def append_to_history(
        self, query_text: str, result_row_count: int, elapsed: float
    ) -> None:
        if self.history is None:
            self.history = History.blank()
        self.history.append(
            query_text=query_text, result_row_count=result_row_count, elapsed=elapsed
        )

    async def on_mount(self) -> None:
        self.run_query_bar.limit_checkbox.value = False

        self._connect()
        self._load_catalog_cache()
        self.action_bind_keymaps(*self.keymap_names)

    @on(Button.Pressed, "#run_query")
    def submit_query_from_run_query_bar(self, message: Button.Pressed) -> None:
        message.stop()
        self.post_message(
            QuerySubmitted(
                query_text=self._get_query_text(),
                limit=self.run_query_bar.limit_value,
            )
        )

    @on(Button.Pressed, "#transaction_button")
    def handle_transaction_button_press(self, message: Button.Pressed) -> None:
        message.stop()
        self.toggle_transaction_mode()
        if self.editor is not None:
            self.editor.focus()

    @on(Button.Pressed, "#commit_button")
    def handle_commit_button_press(self, message: Button.Pressed) -> None:
        message.stop()
        self.commit()
        if self.editor is not None:
            self.editor.focus()

    @on(Button.Pressed, "#rollback_button")
    def handle_rollback_button_press(self, message: Button.Pressed) -> None:
        message.stop()
        self.rollback()
        if self.editor is not None:
            self.editor.focus()

    @on(CatalogCacheLoaded)
    def build_trees(self, message: CatalogCacheLoaded) -> None:
        if self.connection_hash is not None and (
            cached_db := message.cache.get_db(self.connection_hash)
        ):
            self.post_message(NewCatalog(catalog=cached_db))
        if self.show_s3 is not None:
            self.data_catalog.load_s3_tree_from_cache(message.cache)
        if self.connection_hash is not None:
            history = message.cache.get_history(self.connection_hash)
            self.history = history if history is not None else History.blank()

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
        self.post_message(
            TransactionModeChanged(new_mode=message.connection.transaction_mode)
        )
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
        if self.editor is None:
            # recycle message while editor loads
            self.post_message(message=message)
            return
        self.editor.insert_text_at_selection(text=message.insert_name)
        self.editor.focus()

    @on(DataCatalog.NodeCopied)
    def copy_node_name(self, message: DataCatalog.NodeCopied) -> None:
        message.stop()
        if self.editor is None:
            # recycle message while we wait for the editor to load
            self.post_message(message=message)
            return
        self.editor.text_input.clipboard = message.copy_name
        if (
            self.editor.use_system_clipboard
            and self.editor.text_input.system_copy is not None
        ):
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
            self.run_query_bar.run_button.label = "Run Selection"
        else:
            self.run_query_bar.run_button.label = "Run Query"

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
        if self.editor is None:
            # recycle the message while we wait for the editor to load
            self.post_message(message=message)
            return
        # Excel, sheets, and Snowsight all use a TSV format for copying tabular data
        text = os.linesep.join("\t".join(map(str, row)) for row in message.values)
        self.editor.text_input.clipboard = text
        if (
            self.editor.use_system_clipboard
            and self.editor.text_input.system_copy is not None
        ):
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

    @on(QueryError)
    def handle_query_error(self, message: QueryError) -> None:
        self.append_to_history(
            query_text=message.query_text, result_row_count=-1, elapsed=0.0
        )
        self.run_query_bar.set_responsive()
        self.results_viewer.show_table()
        header = getattr(message.error, "title", message.error.__class__.__name__)
        self._push_error_modal(
            title="Query Error",
            header=header,
            error=message.error,
        )

    @on(DataTable.DataLoadError)
    def handle_data_load_error(self, message: DataTable.DataLoadError) -> None:
        header = getattr(message.error, "title", message.error.__class__.__name__)
        self._push_error_modal(
            title="Query Error",
            header=header,
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
        if message.ddl_queries:
            n = len(message.ddl_queries)
            # at least one DDL statement
            elapsed = time.monotonic() - message.submitted_at
            for query_text in message.ddl_queries:
                self.append_to_history(
                    query_text=query_text, result_row_count=0, elapsed=elapsed
                )
            self.notify(
                f"{n} DDL/DML {'query' if n == 1 else 'queries'} "
                f"executed successfully in {elapsed:.2f} seconds."
            )
            self.update_schema_data()

    @on(ResultsFetched)
    async def load_tables(self, message: ResultsFetched) -> None:
        for id_, (cols, data, query_text) in message.data.items():
            table = await self.results_viewer.push_table(
                table_id=id_,
                column_labels=cols,
                data=data,  # type: ignore
            )
            self.append_to_history(
                query_text=query_text,
                result_row_count=table.source_row_count,
                elapsed=message.elapsed,
            )
        if message.errors:
            for _, query_text in message.errors:
                self.append_to_history(
                    query_text=query_text, result_row_count=-1, elapsed=0.0
                )
            header = getattr(
                message.errors[0][0],
                "title",
                "The database raised an error when running your query:",
            )
            self._push_error_modal(
                title="Query Error",
                header=header,
                error=message.errors[0][0],
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

    @on(WidgetMounted)
    def bind_keys(self, message: WidgetMounted) -> None:
        """
        When widgets are first mounted, they will have their default bindings.
        Here we add the bindings from the keymap.
        """
        for keymap_name in self.keymap_names:
            keymap = self._get_keymap(keymap_name=keymap_name)
            if keymap is None:
                continue
            for binding in keymap.bindings:
                action = HARLEQUIN_ACTIONS[binding.action]
                if action.target is not None and isinstance(
                    message.widget, action.target
                ):
                    try:
                        bind(
                            target=message.widget,
                            keys=binding.keys,
                            action=action.action,
                            description=action.description,
                            show=action.show,
                            key_display=binding.key_display,
                            priority=action.priority,
                        )
                    except HarlequinBindingError as e:
                        pretty_print_error(e)
                        self.exit(return_code=2)

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
            if self.data_catalog.has_focus and self.editor is not None:
                self.editor.focus()
        self.data_catalog.disabled = sidebar_hidden

    @on(TransactionModeChanged)
    def update_transaction_button_label(self, message: TransactionModeChanged) -> None:
        message.stop()
        if message.new_mode is not None:
            self.run_query_bar.transaction_button.remove_class("hidden")
            self.run_query_bar.transaction_button.label = (
                f"Tx: {message.new_mode.label}"
            )
            if message.new_mode.commit is not None:
                self.run_query_bar.commit_button.remove_class("hidden")
            else:
                self.run_query_bar.commit_button.add_class("hidden")
            if message.new_mode.rollback is not None:
                self.run_query_bar.rollback_button.remove_class("hidden")
            else:
                self.run_query_bar.rollback_button.add_class("hidden")
        else:
            self.run_query_bar.transaction_button.add_class("hidden")
            self.run_query_bar.commit_button.add_class("hidden")
            self.run_query_bar.rollback_button.add_class("hidden")

    def action_noop(self) -> None:
        """
        A no-op action to unmap keys.
        """
        return

    def action_bind_keymaps(self, *keymap_names: str) -> None:
        """
        Binds the action/key pairs in the keymaps to the currently-mounted
        widgets in Harlequin.
        """
        required_bindings = {"quit": "ctrl+q"}
        self.keymap_names = keymap_names
        for keymap_name in keymap_names:
            keymap = self._get_keymap(keymap_name=keymap_name)
            if keymap is None:
                continue
            for binding in keymap.bindings:
                required_bindings.pop(binding.action, None)
                action = HARLEQUIN_ACTIONS[binding.action]
                if action.target is not None:
                    targets: DOMQuery[Widget] | list[App] = self.query(action.target)
                    if not targets:
                        # some widgets are not yet mounted... we'll get them
                        # by listening for their mount event
                        continue
                else:
                    targets = [self]
                for target in targets:
                    try:
                        bind(
                            target=target,
                            keys=binding.keys,
                            action=action.action,
                            description=action.description,
                            show=action.show,
                            key_display=binding.key_display,
                            priority=action.priority,
                        )
                    except HarlequinBindingError as e:
                        pretty_print_error(e)
                        self.exit(return_code=2)
        for action_name, key in required_bindings.items():
            action = HARLEQUIN_ACTIONS[action_name]
            try:
                bind(
                    target=self,
                    keys=key,
                    action=action.action,
                    description=action.description,
                    show=action.show,
                    key_display=None,
                    priority=action.priority,
                )
            except HarlequinBindingError as e:
                pretty_print_error(e)
                self.exit(return_code=2)

    def action_export(self) -> None:
        show_export_error = partial(
            self._push_error_modal,
            "Export Data Error",
            "Could not export data.",
        )
        table = self.results_viewer.get_visible_table()
        if table is None:
            show_export_error(error=ValueError("You must execute a query first."))
            return
        notify = partial(self.notify, "Data exported successfully.")
        callback = partial(
            export_callback,
            table=table,
            success_callback=notify,
            error_callback=show_export_error,
        )
        self.app.push_screen(
            ExportScreen(
                formats=(
                    WINDOWS_COPY_FORMATS
                    if sys.platform == "win32"
                    else HARLEQUIN_COPY_FORMATS
                ),
                id="export_screen",
            ),
            callback,
        )

    def action_show_query_history(self) -> None:
        async def history_callback(screen_data: str) -> None:
            """
            Insert the selected query into a new buffer.
            """
            await self.editor_collection.insert_buffer_with_text(query_text=screen_data)

        if self.history is None:
            # This should only happen immediately after start-up, before the cache is
            # loaded from disk.
            self._push_error_modal(
                title="History Not Yet Loaded",
                header="Harlequin could not load the Query History.",
                error=ValueError(
                    "Your Query History has not yet been loaded. "
                    "Please wait a moment and try again."
                ),
            )
        elif self.screen.id != "history_screen":
            self.push_screen(
                HistoryScreen(
                    history=self.history,
                    theme=self.theme,
                    id="history_screen",
                ),
                history_callback,
            )

    def action_focus_data_catalog(self) -> None:
        if self.sidebar_hidden or self.data_catalog.disabled:
            self.action_toggle_sidebar()
        self.data_catalog.focus()

    def action_focus_query_editor(self) -> None:
        if self.editor is not None:
            self.editor.focus()

    def action_focus_results_viewer(self) -> None:
        self.results_viewer.focus()

    async def action_quit(self) -> None:
        buffers = []
        for i, editor in enumerate(self.editor_collection.all_editors):
            if editor == self.editor_collection.current_editor:
                focus_index = i
            buffers.append(BufferState(editor.selection, editor.text))
        write_editor_cache(Cache(focus_index=focus_index, buffers=buffers))
        update_catalog_cache(
            connection_hash=self.connection_hash,
            catalog=self.catalog,
            s3_tree=self.data_catalog.s3_tree,
            history=self.history,
        )
        if self.connection:
            self.connection.close()
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
        exit_on_error=True,
        group="query_runners",
        description="Executing queries.",
    )
    def _execute_query(self, message: QuerySubmitted) -> None:
        if self.connection is None:
            return
        cursors: Dict[str, tuple[HarlequinCursor, str]] = {}
        queries = self._split_query_text(message.query_text)
        ddl_queries: list[str] = []
        for q in queries:
            try:
                cur = self.connection.execute(q)
            except HarlequinQueryError as e:
                self.post_message(QueryError(query_text=q, error=e))
                break
            else:
                if cur is not None:
                    if message.limit is not None:
                        cur = cur.set_limit(message.limit)
                    table_id = f"t{hash(cur)}"
                    cursors[table_id] = (cur, q)
                else:
                    ddl_queries.append(q)
        self.post_message(
            QueriesExecuted(
                query_count=len(cursors) + len(ddl_queries),
                cursors=cursors,
                submitted_at=message.submitted_at,
                ddl_queries=ddl_queries,
            )
        )

    def _get_query_text(self) -> str:
        if self.editor is None:
            return ""
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
        self,
        cursors: Dict[str, tuple[HarlequinCursor, str]],
        submitted_at: float,
    ) -> None:
        errors: list[tuple[BaseException, str]] = []
        data: Dict[str, tuple[list[tuple[str, str]], AutoBackendType | None, str]] = {}
        for id_, (cur, q) in cursors.items():
            try:
                cur_data = cur.fetchall()
            except HarlequinQueryError as e:
                errors.append((e, q))
            else:
                data[id_] = (cur.columns(), cur_data, q)
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
        if self.editor is None:
            return ""
        selection = self.editor.selected_text.strip()
        if self.connection is None:
            return selection
        if selection:
            try:
                return self.connection.validate_sql(selection)
            except NotImplementedError:
                return selection
        else:
            return ""

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="transaction_togglers",
    )
    def toggle_transaction_mode(self) -> None:
        if self.connection is not None:
            new_mode = self.connection.toggle_transaction_mode()
            self.post_message(TransactionModeChanged(new_mode=new_mode))

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="commit-rollback",
    )
    def commit(self) -> None:
        if (
            self.connection is not None
            and self.connection.transaction_mode is not None
            and self.connection.transaction_mode.commit is not None
        ):
            started_at = time.monotonic()
            try:
                self.connection.transaction_mode.commit()
            except Exception as e:
                self._push_error_modal(
                    title="Transaction Error",
                    header="Harlequin could not commit the transaction.",
                    error=e,
                )
            else:
                elapsed = time.monotonic() - started_at
                self.notify(f"Transaction committed in {elapsed:.2f} seconds.")
                self.update_schema_data()

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="commit-rollback",
    )
    def rollback(self) -> None:
        if (
            self.connection is not None
            and self.connection.transaction_mode is not None
            and self.connection.transaction_mode.rollback is not None
        ):
            started_at = time.monotonic()
            try:
                self.connection.transaction_mode.rollback()
            except Exception as e:
                self._push_error_modal(
                    title="Transaction Error",
                    header="Harlequin could not roll back the transaction.",
                    error=e,
                )
            else:
                elapsed = time.monotonic() - started_at
                self.notify(f"Transaction rolled back in {elapsed:.2f} seconds.")
                self.update_schema_data()

    def _get_keymap(self, keymap_name: str) -> "HarlequinKeyMap" | None:
        try:
            keymap = self.all_keymaps[keymap_name]
        except KeyError as e:
            self.exit(
                return_code=2,
                message=pretty_error_message(
                    HarlequinConfigError(
                        title="Could not bind keymap",
                        msg=(
                            f"Harlequin could not find a keymap named {e}, "
                            f"either as a plug-in or user-defined keymap. You may "
                            "need to install it before specifying it as an option."
                        ),
                    )
                ),
            )
            # for some reason this doesn't exit right away...
            keymap = None
        return keymap
