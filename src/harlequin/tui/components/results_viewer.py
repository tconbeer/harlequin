from typing import Union

from textual.app import ComposeResult
from textual.widgets import ContentSwitcher, DataTable, LoadingIndicator


class ResultsTable(DataTable):
    DEFAULT_CSS = """
        ResultsTable {
            height: 100%;
            width: 100%;
        }
    """


class ResultsViewer(ContentSwitcher):
    TABLE_ID = "data"
    LOADING_ID = "loading"

    def compose(self) -> ComposeResult:
        yield ResultsTable(id=self.TABLE_ID)
        yield LoadingIndicator(id=self.LOADING_ID)

    def on_mount(self) -> None:
        self.border_title = "Query Results"
        self.current = self.TABLE_ID

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

    def increment_progress_bar(self) -> None:
        self.border_title = f"{self.border_title}."

    def set_responsive(
        self,
        max_rows: Union[int, None] = None,
        total_rows: Union[int, None] = None,
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
        self.remove_class("non-responsive")

    def show_table(self) -> None:
        self.current = self.TABLE_ID

    def get_table(self) -> ResultsTable:
        table = self.get_child_by_id(self.TABLE_ID)
        assert isinstance(table, ResultsTable)
        return table

    def show_loading(self) -> None:
        self.current = self.LOADING_ID

    def get_loading(self) -> LoadingIndicator:
        loading = self.get_child_by_id(self.LOADING_ID)
        assert isinstance(loading, LoadingIndicator)
        return loading
