from typing import Type
from textual.app import App, CSSPathType, ComposeResult
from textual.driver import Driver
from textual.widgets import Header, Footer

from harlequin.tui import SchemaViewer, ResultsViewer, CodeEditor
from duckdb import DuckDBPyConnection


class SqlClient(App):
    """
    A Textual App for a SQL client for DuckDB.
    """

    CSS_PATH = "sql_client.css"

    def __init__(
        self,
        connection: DuckDBPyConnection,
        driver_class: Type[Driver] | None = None,
        css_path: CSSPathType | None = None,
        watch_css: bool = False,
    ):
        self.connection = connection
        super().__init__(driver_class, css_path, watch_css)

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield SchemaViewer("Data", connection=self.connection, id="sidebar")
        yield CodeEditor(placeholder="Code")
        yield ResultsViewer()
        yield Footer()

    def on_input_submitted(self, message: CodeEditor.Submitted) -> None:
        table = self.query_one(ResultsViewer)
        table.clear(columns=True)
        data = self.connection.sql(message.value)
        table.add_columns(*data.columns)  # type: ignore
        table.add_rows(data.fetchall())


if __name__ == "__main__":
    import duckdb

    conn = duckdb.connect("dev.db")
    app = SqlClient(conn)
    app.run()
