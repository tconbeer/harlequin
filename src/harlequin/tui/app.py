from pathlib import Path
from typing import Type

import duckdb
from textual import work
from textual.app import App, ComposeResult, CSSPathType
from textual.containers import Container
from textual.driver import Driver
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input
from textual.worker import Worker, get_current_worker

from harlequin.tui.components import (
    SCHEMAS,
    TABLES,
    CodeEditor,
    ErrorModal,
    ResultsViewer,
    SchemaViewer,
)


class Harlequin(App):
    """
    A Textual App for a SQL client for DuckDB.
    """

    CSS_PATH = "app.css"

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
        with Container(id="sql_client"):
            yield Header()
            yield SchemaViewer(self.db_name, connection=self.connection)
            yield CodeEditor(placeholder="select ...")
            yield ResultsViewer()
            yield Footer()

    def on_mount(self) -> None:
        editor = self.query_one(CodeEditor)
        self.set_focus(editor)
        self.update_schema_data()

    def on_input_submitted(self, message: Input.Submitted) -> None:
        if message.input.id == "save_modal":
            self.app.pop_screen()
            try:
                with open(message.input.value, "w") as f:
                    editor = self.query_one(CodeEditor)
                    f.write(editor.value)
            except OSError as e:
                self.push_screen(
                    ErrorModal(
                        header=(
                            "Harlequin encountered an error when "
                            "attempting to save your file:"
                        ),
                        error=e,
                    )
                )
        elif message.input.id == "load_modal":
            self.app.pop_screen()
            try:
                with open(message.input.value, "r") as f:
                    editor = self.query_one(CodeEditor)
                    editor.value = f.read()
                    editor.action_end()
            except OSError as e:
                self.push_screen(
                    ErrorModal(
                        header=(
                            "Harlequin encountered an error when "
                            "attempting to load your file:"
                        ),
                        error=e,
                    )
                )
        else:
            try:
                self.relation = self.connection.sql(message.value)
            except duckdb.Error as e:
                self.push_screen(
                    ErrorModal(
                        header=(
                            "DuckDB raised an error when compiling "
                            "or running your query:"
                        ),
                        error=e,
                    )
                )

    def set_data(self, data: list[tuple]) -> None:
        self.data = data

    async def watch_relation(self, relation: duckdb.DuckDBPyRelation | None) -> None:
        table = self.query_one(ResultsViewer)
        table.clear(columns=True)
        if relation is not None:  # select query
            table.add_columns(*relation.columns)
            worker = self.fetch_relation_data(relation)
            await worker.wait()
        else:  # DDL/DML query or an error
            self.data = []
        self.update_schema_data()

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
