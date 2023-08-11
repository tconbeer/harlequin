from typing import Union

import duckdb
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import (
    ContentSwitcher,
    DataTable,
    LoadingIndicator,
    TabbedContent,
    TabPane,
)

from harlequin.tui.utils import short_type


class ResultsTable(DataTable):
    DEFAULT_CSS = """
        ResultsTable {
            height: 100%;
            width: 100%;
        }
    """


class ResultsViewer(ContentSwitcher, can_focus=True):
    TABBED_ID = "tabs"
    LOADING_ID = "loading"

    def compose(self) -> ComposeResult:
        yield TabbedContent(id=self.TABBED_ID)
        yield LoadingIndicator(id=self.LOADING_ID)

    def on_mount(self) -> None:
        self.border_title = "Query Results"
        self.current = self.TABBED_ID
        self.tab_switcher = self.query_one(TabbedContent)

    def on_focus(self) -> None:
        content = self.tab_switcher.query_one(ContentSwitcher)
        active_tab_id = self.tab_switcher.active
        if active_tab_id:
            tab_pane = content.query_one(f"#{active_tab_id}", TabPane)
            tab_pane.query_one(ResultsTable).focus()
        else:
            tables = content.query(ResultsTable)
            if tables:
                tables[0].focus()

    def set_not_responsive(
        self, max_rows: Union[int, None] = None, total_rows: Union[int, None] = None
    ) -> None:
        if (total_rows and not max_rows) or (
            total_rows and max_rows and total_rows <= max_rows
        ):
            self.border_title = f"LOADING {total_rows:,} Records."
        elif total_rows and max_rows:
            self.border_title = f"LOADING {max_rows:,} of {total_rows:,} Records."
        else:
            self.border_title = "Running Query"
        self.add_class("non-responsive")
        self.remove_class("hide-tabs")

    def increment_progress_bar(self) -> None:
        self.border_title = f"{self.border_title}."

    def set_responsive(
        self,
        max_rows: Union[int, None] = None,
        total_rows: Union[int, None] = None,
        num_queries: Union[int, None] = None,
        did_run: bool = True,
    ) -> None:
        if (total_rows and not max_rows) or (
            total_rows and max_rows and total_rows <= max_rows
        ):
            self.border_title = f"Query Results ({total_rows:,} Records)"
        elif total_rows and max_rows:
            self.border_title = (
                f"Query Results (Showing {max_rows:,} of {total_rows:,} Records)."
            )
        elif not did_run:
            self.border_title = "Query Results"
        else:
            self.border_title = "Query Returned No Records"
        if num_queries is None or num_queries < 2:
            self.add_class("hide-tabs")
        self.remove_class("non-responsive")

    def show_table(self) -> None:
        self.current = self.TABBED_ID

    def push_table(self, relation: duckdb.DuckDBPyRelation) -> str:
        table_id = f"t{hash(relation)}"
        table = ResultsTable(id=table_id)
        short_types = [short_type(t) for t in relation.dtypes]
        table.add_columns(
            *[
                f"{name} [#888888]{data_type}[/]"
                for name, data_type in zip(relation.columns, short_types)
            ]
        )
        pane = TabPane(f"Result {self.tab_switcher.tab_count+1}", table)
        self.tab_switcher.add_pane(pane)
        self.log_children_height(self.tab_switcher)

        return table_id

    def log_children_height(self, w: Widget) -> None:
        self.log(f"CHILDREN od {w}: ", w.children)
        for child in w.children:
            assert child is not None
            self.log("CHILD: ", child, child.size)
            if child.children:
                self.log_children_height(child)

    def clear_all_tables(self) -> None:
        self.tab_switcher.clear_panes()

    def show_loading(self) -> None:
        self.current = self.LOADING_ID

    def get_loading(self) -> LoadingIndicator:
        loading = self.get_child_by_id(self.LOADING_ID, expect_type=LoadingIndicator)
        return loading
