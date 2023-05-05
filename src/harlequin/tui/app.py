from pathlib import Path
from typing import Iterator, Type

import duckdb
from textual import log, work
from textual.app import App, ComposeResult, CSSPathType
from textual.containers import Container
from textual.driver import Driver
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input
from textual.worker import Worker, WorkerFailed, get_current_worker

from harlequin.tui.components import (
    SCHEMAS,
    TABLES,
    CodeEditor,
    ErrorModal,
    ResultsTable,
    ResultsViewer,
    SchemaViewer,
    TextInput,
)


class Harlequin(App):
    """
    A Textual App for a SQL client for DuckDB.
    """

    CSS_PATH = "app.css"
    MAX_RESULTS = 50_000

    query_text: reactive[str] = reactive(str)
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
            yield CodeEditor()
            yield ResultsViewer()
            yield Footer()

    async def on_mount(self) -> None:
        worker = self.update_schema_data()
        editor = self.query_one(TextInput)
        self.set_focus(editor)
        await worker.wait()

    def on_code_editor_submitted(self, message: CodeEditor.Submitted) -> None:
        self.query_text = "\n".join(message.lines)

    def on_input_submitted(self, message: Input.Submitted) -> None:
        self.app.pop_screen()
        editor = self.query_one(TextInput)
        if message.input.id == "save_modal":
            try:
                with open(message.input.value, "w") as f:
                    query = "\n".join([line.rstrip() for line in editor.lines])
                    f.write(query)
            except OSError as e:
                self.push_screen(
                    ErrorModal(
                        title="Save File Error",
                        header=(
                            "Harlequin encountered an error when "
                            "attempting to save your file:"
                        ),
                        error=e,
                    )
                )
        elif message.input.id == "load_modal":
            try:
                with open(message.input.value, "r") as f:
                    query = f.read()
            except OSError as e:
                self.push_screen(
                    ErrorModal(
                        title="Load File Error",
                        header=(
                            "Harlequin encountered an error when "
                            "attempting to load your file:"
                        ),
                        error=e,
                    )
                )
            else:
                editor.move_cursor(0, 0)
                editor.lines = [f"{line} " for line in query.splitlines()]

    def set_data(self, data: list[tuple]) -> None:
        log(f"set_data {len(data)}")
        self.data = data

    async def watch_query_text(self, query_text: str) -> None:
        pane = self.query_one(ResultsViewer)
        pane.show_loading()
        pane.set_not_responsive()
        try:
            worker = self.build_relation(query_text)
            await worker.wait()
        except WorkerFailed as e:
            pane.show_table()
            self.push_screen(
                ErrorModal(
                    title="DuckDB Error",
                    header=(
                        "DuckDB raised an error when compiling "
                        "or running your query:"
                    ),
                    error=e.error,
                )
            )
        else:
            table = pane.get_table()
            table.clear(columns=True)
            relation = worker.result
            if relation:  # select query
                self.relation = relation
            elif bool(query_text.strip()):  # DDL/DML query
                pane.set_responsive()
                pane.show_table()
                self.data = []
                self.update_schema_data()
            else:  # blank query
                pane.set_responsive(did_run=False)
                pane.show_table()
                self.data = []

    def watch_relation(self, relation: duckdb.DuckDBPyRelation | None) -> None:
        """
        Only runs for select statements, except when first mounted.
        """
        # invalidate results so watch_data runs even if the results are the same
        self.data = []
        if relation is not None:
            table = self.query_one(ResultsViewer).get_table()
            table.add_columns(*relation.columns)
            self.fetch_relation_data(relation)

    async def watch_data(self, data: list[tuple]) -> None:
        if data:
            pane = self.query_one(ResultsViewer)
            pane.set_not_responsive(max_rows=self.MAX_RESULTS, total_rows=len(data))
            table = pane.get_table()
            for i, chunk in self.chunk(data[: self.MAX_RESULTS]):
                worker = self.add_data_to_table(table, chunk)
                await worker.wait()
                pane.increment_progress_bar()
                if i == 0:
                    pane.show_table()
            pane.set_responsive(max_rows=self.MAX_RESULTS, total_rows=len(data))

    @staticmethod
    def chunk(
        data: list[tuple], chunksize: int = 2000
    ) -> Iterator[tuple[int, list[tuple]]]:
        log(f"chunk {len(data)}")
        for i in range(len(data) // chunksize + 1):
            log(f"yielding chunk {i}")
            yield i, data[i * chunksize : (i + 1) * chunksize]

    @work(exclusive=True, exit_on_error=False)  # type: ignore
    def build_relation(self, query_text: str) -> duckdb.DuckDBPyRelation | None:
        relation = self.connection.sql(query_text)
        return relation

    @work(exclusive=True)
    def fetch_relation_data(self, relation: duckdb.DuckDBPyRelation) -> None:
        log(f"fetch_relation_data {hash(relation)}")
        data = relation.fetchall()
        log(f"fetch_relation_data FINISHED {hash(relation)}")
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.call_from_thread(self.set_data, data)

    @work(exclusive=False)
    def add_data_to_table(self, table: ResultsTable, data: list[tuple]) -> Worker:
        log(f"add_data_to_table {len(data)}")
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.call_from_thread(table.add_rows, data)
        return worker

    @work(exclusive=True)
    def update_schema_data(self) -> None:
        log("update_schema_data")
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
    app = Harlequin(Path("f1.db"))
    app.run()
