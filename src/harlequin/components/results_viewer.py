from typing import List, Tuple, Union

from rich.markup import escape
from textual.css.query import NoMatches
from textual.widgets import (
    ContentSwitcher,
    TabbedContent,
    TabPane,
    Tabs,
)
from textual_fastdatatable import DataTable
from textual_fastdatatable.backend import AutoBackendType

from harlequin.messages import WidgetMounted


class ResultsTable(DataTable, inherit_bindings=False):
    DEFAULT_CSS = """
        ResultsTable {
            height: 100%;
            width: 100%;
        }
    """

    def on_mount(self) -> None:
        self.post_message(WidgetMounted(widget=self))


class ResultsViewer(TabbedContent, can_focus=True):
    BORDER_TITLE = "Query Results"

    def __init__(
        self,
        max_results: int = 10_000,
        type_color: str = "#888888",
    ) -> None:
        super().__init__()
        self.max_results = max_results
        self.type_color = type_color

    def on_mount(self) -> None:
        self.query_one(Tabs).can_focus = False
        self.add_class("hide-tabs")
        self.max_col_width = self._get_max_col_width()
        self.post_message(WidgetMounted(widget=self))

    def clear_all_tables(self) -> None:
        self.clear_panes()
        self.add_class("hide-tabs")

    def get_visible_table(self) -> Union[ResultsTable, None]:
        content = self.query_one(ContentSwitcher)
        active_tab_id = self.active
        if active_tab_id:
            try:
                tab_pane = content.query_one(f"#{active_tab_id}", TabPane)
                return tab_pane.query_one(ResultsTable)
            except NoMatches:
                return None
        else:
            tables = content.query(ResultsTable)
            try:
                return tables.first(ResultsTable)
            except NoMatches:
                self.log("NO TABLES FOUND")
                return None

    async def push_table(
        self,
        table_id: str,
        column_labels: List[Tuple[str, str]],
        data: AutoBackendType,
    ) -> ResultsTable:
        formatted_labels = [
            self._format_column_label(col_name, col_type)
            for col_name, col_type in column_labels
        ]
        table = ResultsTable(
            id=table_id,
            column_labels=formatted_labels,  # type: ignore
            data=data,
            max_rows=self.max_results,
            cursor_type="range",
            max_column_content_width=self.max_col_width,
            null_rep="[dim]∅ null[/]",
        )
        n = self.tab_count + 1
        if n > 1:
            self.remove_class("hide-tabs")
        pane = TabPane(f"Result {n}", table)
        await self.add_pane(pane)
        self.active = f"tab-{n}"
        # need to manually refresh the table, since activating the tab
        # doesn't consistently cause a new layout calc.
        table.refresh(repaint=True, layout=True)
        return table

    def show_loading(self) -> None:
        self.border_title = "Running Query"
        self.add_class("non-responsive")
        self.loading = True
        self.clear_all_tables()

    def show_table(self, did_run: bool = True) -> None:
        self.loading = False
        self.remove_class("non-responsive")
        if not did_run:
            self.border_title = "Query Results"
        else:
            table = self.get_visible_table()
            if table is not None:
                rows = table.source_row_count
                if rows > 0:
                    self.border_title = (
                        f"Query Results {self._human_row_count(table.source_row_count)}"
                    )
                else:
                    self.border_title = "Query Returned No Records"
            else:
                self.border_title = "Query Results"

    def on_focus(self) -> None:
        self._focus_on_visible_table()

    def on_resize(self) -> None:
        # only impacts new tables pushed after the resize
        self.max_col_width = self._get_max_col_width()

    def on_tabbed_content_tab_activated(
        self, message: TabbedContent.TabActivated
    ) -> None:
        message.stop()
        maybe_table = self.get_visible_table()
        if maybe_table is not None:
            self.border_title = (
                f"Query Results {self._human_row_count(maybe_table.source_row_count)}"
            )
            maybe_table.focus()

    def action_switch_tab(self, offset: int) -> None:
        if not self.active:
            return
        tab_number = int(self.active.split("-")[1])
        unsafe_tab_number = tab_number + offset
        if unsafe_tab_number < 1:
            new_tab_number = self.tab_count
        elif unsafe_tab_number > self.tab_count:
            new_tab_number = 1
        else:
            new_tab_number = unsafe_tab_number
        self.active = f"tab-{new_tab_number}"
        self._focus_on_visible_table()

    def action_focus_data_catalog(self) -> None:
        if hasattr(self.app, "action_focus_data_catalog"):
            self.app.action_focus_data_catalog()

    def action_focus_query_editor(self) -> None:
        if hasattr(self.app, "action_focus_query_editor"):
            self.app.action_focus_query_editor()

    def _focus_on_visible_table(self) -> None:
        maybe_table = self.get_visible_table()
        if maybe_table is not None:
            maybe_table.focus()

    def _human_row_count(self, total_rows: int) -> str:
        if self.max_results > 0 and total_rows > self.max_results:
            return f"(Showing {self.max_results:,} of {total_rows:,} Records)"
        else:
            return f"({total_rows:,} Records)"

    def _format_column_label(self, col_name: str, col_type: str) -> str:
        return f"{escape(col_name)} [{self.type_color}]{escape(col_type)}[/]"

    def _get_max_col_width(self) -> int:
        SMALLEST_MAX_WIDTH = 20
        CELL_X_PADDING = 2
        parent_size = getattr(self.parent, "container_size", self.screen.container_size)
        return max(SMALLEST_MAX_WIDTH, parent_size.width // 2 - CELL_X_PADDING)
