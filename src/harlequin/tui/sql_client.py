from pathlib import Path
from typing import Type

import duckdb
from textual import work
from textual.app import App, ComposeResult, CSSPathType
from textual.driver import Driver
from textual.reactive import reactive
from textual.widgets import Footer, Header
from textual.worker import Worker, get_current_worker

from harlequin.tui import SCHEMAS, TABLES, CodeEditor, ResultsViewer, SchemaViewer


class Harlequin(App):
    """
    A Textual App for a SQL client for DuckDB.
    """

    CSS_PATH = "sql_client.css"

    relation: reactive[duckdb.DuckDBPyRelation | None] = reactive(None)
    data: reactive[list[tuple]] = reactive(list)

    def __init__(
        self,
        db_path: Path,
        driver_class: Type[Driver] | None = None,
        css_path: CSSPathType | None = None,
        watch_css: bool = False,
    ):
        self.db_name = db_path.stem
        self.connection = duckdb.connect(database=str(db_path))
        super().__init__(driver_class, css_path, watch_css)

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield SchemaViewer(self.db_name, connection=self.connection)
        yield CodeEditor(placeholder="Code")
        yield ResultsViewer()
        yield Footer()

    def on_mount(self) -> None:
        editor = self.query_one(CodeEditor)
        self.set_focus(editor)
        self.update_schema_data()

    def on_input_submitted(self, message: CodeEditor.Submitted) -> None:
        try:
            self.relation = self.connection.sql(message.value)
        except duckdb.Error:
            self.relation = None

    def set_data(self, data: list[tuple]) -> None:
        self.data = data

    async def watch_relation(self, relation: duckdb.DuckDBPyRelation | None) -> None:
        table = self.query_one(ResultsViewer)
        table.clear(columns=True)
        if relation is not None:
            table.add_columns(*relation.columns)
            worker = self.fetch_relation_data(relation)
            await worker.wait()
            self.update_schema_data()
        else:
            self.data = []

    def watch_data(self, data: list[tuple]) -> None:
        if data:
            table = self.query_one(ResultsViewer)
            table.add_rows(data)

    @work(exclusive=False)
    def fetch_relation_data(self, relation: duckdb.DuckDBPyRelation) -> Worker:
        data = relation.fetchall()
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.set_data(data)
        return worker

    @work(exclusive=False)
    def update_schema_data(self) -> None:
        data: SCHEMAS = []
        schemas = self.connection.execute(
            "select distinct table_schema "
            "from information_schema.tables "
            "order by 1"
        ).fetchall()
        for (schema,) in schemas:
            tables = self.connection.execute(
                "select table_name, table_type "
                "from information_schema.tables "
                "where table_schema = ?"
                "order by 1",
                [schema],
            ).fetchall()
            tables_data: TABLES = []
            if tables:
                for table, type in tables:
                    columns = self.connection.execute(
                        "select column_name, data_type "
                        "from information_schema.columns "
                        "where table_schema = ? and table_name = ? "
                        "order by 1",
                        [schema, table],
                    ).fetchall()
                    tables_data.append((table, type, columns))
            data.append((schema, tables_data))
        schema_viewer = self.query_one(SchemaViewer)
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.call_from_thread(schema_viewer.update_tree, data)


if __name__ == "__main__":
    app = Harlequin(Path("dev.db"))
    app.run()
