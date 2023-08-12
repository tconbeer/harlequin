from typing import Dict, Iterator, List, Tuple, Union

import duckdb
from textual import work
from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    ContentSwitcher,
    DataTable,
    LoadingIndicator,
    TabbedContent,
    TabPane,
)
from textual.worker import Worker, get_current_worker

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
    data: reactive[Dict[str, List[Tuple]]] = reactive(dict)

    class Ready(Message):
        pass

    def __init__(
        self,
        *children: Widget,
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa A002
        classes: Union[str, None] = None,
        disabled: bool = False,
        initial: Union[str, None] = None,
        max_results: int = 10_000,
    ) -> None:
        super().__init__(
            *children,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            initial=initial,
        )
        self.MAX_RESULTS = max_results

    def compose(self) -> ComposeResult:
        yield TabbedContent(id=self.TABBED_ID)
        yield LoadingIndicator(id=self.LOADING_ID)

    def on_mount(self) -> None:
        self.border_title = "Query Results"
        self.current = self.TABBED_ID
        self.tab_switcher = self.query_one(TabbedContent)

    def on_focus(self) -> None:
        maybe_table = self.get_visible_table()
        if maybe_table is not None:
            maybe_table.focus()

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

    async def watch_data(self, data: Dict[str, List[Tuple]]) -> None:
        if data:
            self.set_not_responsive(max_rows=self.MAX_RESULTS, total_rows=len(data))
            for table_id, result in data.items():
                table = self.tab_switcher.query_one(f"#{table_id}", ResultsTable)
                for i, chunk in self.chunk(result[: self.MAX_RESULTS]):
                    worker = self.add_data_to_table(table, chunk)
                    await worker.wait()
                    self.increment_progress_bar()
                    if i == 0:
                        self.show_table()
            else:
                self.set_responsive(
                    max_rows=self.MAX_RESULTS,
                    total_rows=len(data[table_id]),
                    num_queries=len(data),
                )
            self.post_message(self.Ready())
            self.focus()

    @staticmethod
    def chunk(
        data: List[Tuple], chunksize: int = 2000
    ) -> Iterator[Tuple[int, List[Tuple]]]:
        for i in range(len(data) // chunksize + 1):
            yield i, data[i * chunksize : (i + 1) * chunksize]

    @work(exclusive=False)
    async def add_data_to_table(self, table: ResultsTable, data: List[Tuple]) -> Worker:
        worker = get_current_worker()
        if not worker.is_cancelled:
            table.add_rows(data)
        return worker

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

    def push_table(self, table_id: str, relation: duckdb.DuckDBPyRelation) -> None:
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

    def clear_all_tables(self) -> None:
        self.tab_switcher.clear_panes()

    def show_loading(self) -> None:
        self.current = self.LOADING_ID

    def get_loading(self) -> LoadingIndicator:
        loading = self.get_child_by_id(self.LOADING_ID, expect_type=LoadingIndicator)
        return loading
