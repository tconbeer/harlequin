from typing import List, Tuple, Union

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import (
    ContentSwitcher,
    LoadingIndicator,
    TabbedContent,
    TabPane,
    Tabs,
)
from textual_fastdatatable import DataTable
from textual_fastdatatable.backend import AutoBackendType


class ResultsTable(DataTable):
    DEFAULT_CSS = """
        ResultsTable {
            height: 100%;
            width: 100%;
        }
    """


class ResultsViewer(ContentSwitcher, can_focus=True):
    BINDINGS = [
        Binding("j", "switch_tab(-1)", "Previous Tab", show=False),
        Binding("k", "switch_tab(1)", "Next Tab", show=False),
    ]

    TABBED_ID = "tabs"
    LOADING_ID = "loading"

    def __init__(
        self,
        *children: Widget,
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa A002
        classes: Union[str, None] = None,
        disabled: bool = False,
        initial: Union[str, None] = None,
        max_results: int = 10_000,
        type_color: str = "#888888",
    ) -> None:
        super().__init__(
            *children,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            initial=initial,
        )
        self.max_results = max_results
        self.type_color = type_color

    def compose(self) -> ComposeResult:
        yield TabbedContent(id=self.TABBED_ID)
        yield LoadingIndicator(id=self.LOADING_ID)

    def clear_all_tables(self) -> None:
        self.tab_switcher.clear_panes()
        self.add_class("hide-tabs")

    def get_visible_table(self) -> Union[ResultsTable, None]:
        content = self.tab_switcher.query_one(ContentSwitcher)
        active_tab_id = self.tab_switcher.active
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
                return None

    async def push_table(
        self,
        table_id: str,
        column_labels: List[Tuple[str, str]],
        data: AutoBackendType,
    ) -> None:
        formatted_labels = [
            self._format_column_label(col_name, col_type)
            for col_name, col_type in column_labels
        ]
        self.app.log(f"LABELS: {formatted_labels}")
        table = ResultsTable(
            id=table_id,
            column_labels=formatted_labels,  # type: ignore
            data=data,
            max_rows=self.max_results,
        )
        n = self.tab_switcher.tab_count + 1
        if n > 1:
            self.remove_class("hide-tabs")
        pane = TabPane(f"Result {n}", table)
        await self.tab_switcher.add_pane(pane)  # type: ignore
        self.tab_switcher.active = f"tab-{n}"

    async def set_not_responsive(self) -> None:
        self.border_title = "Loading Data"
        self.add_class("non-responsive")

    def set_responsive(
        self,
        did_run: bool = True,
    ) -> None:
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
        self.remove_class("non-responsive")

    def show_loading(self) -> None:
        self.current = self.LOADING_ID
        self.border_title = "Running Query"
        self.add_class("non-responsive")

    def show_table(self) -> None:
        self.current = self.TABBED_ID

    def on_mount(self) -> None:
        self.border_title = "Query Results"
        self.current = self.TABBED_ID
        self.tab_switcher = self.query_one(TabbedContent)
        self.loading_spinner = self.query_one(LoadingIndicator)
        self.query_one(Tabs).can_focus = False

    def on_focus(self) -> None:
        self._focus_on_visible_table()

    def on_tabbed_content_tab_activated(
        self, message: TabbedContent.TabActivated
    ) -> None:
        message.stop()
        # Don't update the border if we're still loading the table.
        if self.border_title and str(self.border_title).startswith("Loading"):
            return
        maybe_table = self.get_visible_table()
        if maybe_table is not None:
            self.border_title = (
                f"Query Results {self._human_row_count(maybe_table.source_row_count)}"
            )
            maybe_table.focus()

    def action_switch_tab(self, offset: int) -> None:
        if not self.tab_switcher.active:
            return
        tab_number = int(self.tab_switcher.active.split("-")[1])
        unsafe_tab_number = tab_number + offset
        if unsafe_tab_number < 1:
            new_tab_number = self.tab_switcher.tab_count
        elif unsafe_tab_number > self.tab_switcher.tab_count:
            new_tab_number = 1
        else:
            new_tab_number = unsafe_tab_number
        self.tab_switcher.active = f"tab-{new_tab_number}"
        self._focus_on_visible_table()

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
