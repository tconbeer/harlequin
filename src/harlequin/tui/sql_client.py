from typing import Type
from textual.app import App, CSSPathType, ComposeResult
from textual.driver import Driver
from textual.widgets import Header, Footer, Static
from textual.containers import Container, Vertical

from harlequin.tui.schema_viewer import SchemaViewer
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
        yield Static("Code")
        yield Static("Data")
        yield Footer()
