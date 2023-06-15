from pathlib import Path
from typing import Iterator, List, Tuple, Type, Union

import duckdb
from textual import log, work
from textual.app import App, ComposeResult, CSSPathType
from textual.containers import Container
from textual.driver import Driver
from textual.reactive import reactive
from textual.widgets import Footer, Header
from textual.worker import Worker, WorkerFailed, get_current_worker
from textual_textarea import TextArea

from harlequin.tui.components import (
    DATABASES,
    SCHEMAS,
    TABLES,
    CodeEditor,
    ErrorModal,
    ResultsTable,
    ResultsViewer,
    SchemaViewer,
)
from harlequin.tui.utils import short_type


class Harlequin(App, inherit_bindings=False):
    """
    A Textual App for a SQL client for DuckDB.
    """

    CSS_PATH = "app.css"
    MAX_RESULTS = 50_000

    BINDINGS = [("ctrl+q", "quit", "Quit")]

    query_text: reactive[str] = reactive(str)
    relation: reactive[Union[duckdb.DuckDBPyRelation, None]] = reactive(None)
    data: reactive[List[Tuple]] = reactive(list)

    def __init__(
        self,
        db_path: List[Path],
        read_only: bool = False,
        theme: str = "monokai",
        driver_class: Union[Type[Driver], None] = None,
        css_path: Union[CSSPathType, None] = None,
        watch_css: bool = False,
    ):
        super().__init__(driver_class, css_path, watch_css)
        self.theme = theme
        if not db_path:
            db_path = [Path(":memory:")]
        primary_db, *other_dbs = db_path
        try:
            self.connection = duckdb.connect(
                database=str(primary_db), read_only=read_only
            )
            for db in other_dbs:
                self.connection.execute(
                    f"attach '{db}'{ '(READ ONLY)' if read_only else ''}"
                )
        except (duckdb.CatalogException, duckdb.IOException) as e:
            from rich import print
            from rich.panel import Panel

            print(
                Panel.fit(
                    str(e),
                    title="DuckDB couldn't connect to your database.",
                    title_align="left",
                    border_style="red",
                    subtitle="Try again?",
                    subtitle_align="right",
                )
            )
            self.exit()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        with Container(id="sql_client"):
            yield Header()
            yield SchemaViewer("Data Catalog", connection=self.connection)
            yield CodeEditor(language="sql", theme=self.theme)
            yield ResultsViewer()
            yield Footer()

    async def on_mount(self) -> None:
        worker = self.update_schema_data()
        editor = self.query_one(TextArea)
        self.set_focus(editor)
        await worker.wait()

    def on_code_editor_submitted(self, message: CodeEditor.Submitted) -> None:
        self.query_text = message.text

    def set_data(self, data: List[Tuple]) -> None:
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

    async def watch_relation(
        self, relation: Union[duckdb.DuckDBPyRelation, None]
    ) -> None:
        """
        Only runs for select statements, except when first mounted.
        """
        # invalidate results so watch_data runs even if the results are the same
        self.data = []
        if relation is not None:
            pane = self.query_one(ResultsViewer)
            table = pane.get_table()
            short_types = [short_type(t) for t in relation.dtypes]
            table.add_columns(
                *[
                    f"{name} [#888888]{type}[/]"
                    for name, type in zip(relation.columns, short_types)
                ]
            )
            try:
                worker = self.fetch_relation_data(relation)
                await worker.wait()
            except WorkerFailed as e:
                self.push_screen(
                    ErrorModal(
                        title="DuckDB Error",
                        header=("DuckDB raised an error when " "running your query:"),
                        error=e.error,
                    )
                )
                pane.show_table()
            # Textual fails to catch some duckdb Errors,
            # so we need this mostly- redundant block.
            except duckdb.Error as e:
                self.push_screen(
                    ErrorModal(
                        title="DuckDB Error",
                        header=("DuckDB raised an error when " "running your query:"),
                        error=e,
                    )
                )
                pane.show_table()

    async def watch_data(self, data: List[Tuple]) -> None:
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
            table.focus()

    @staticmethod
    def chunk(
        data: List[Tuple], chunksize: int = 2000
    ) -> Iterator[Tuple[int, List[Tuple]]]:
        log(f"chunk {len(data)}")
        for i in range(len(data) // chunksize + 1):
            log(f"yielding chunk {i}")
            yield i, data[i * chunksize : (i + 1) * chunksize]

    @work(exclusive=True, exit_on_error=False)  # type: ignore
    def build_relation(self, query_text: str) -> Union[duckdb.DuckDBPyRelation, None]:
        relation = self.connection.sql(query_text)
        return relation

    @work(exclusive=True, exit_on_error=False)  # type: ignore
    def fetch_relation_data(self, relation: duckdb.DuckDBPyRelation) -> None:
        log(f"fetch_relation_data {hash(relation)}")
        data = relation.fetchall()
        log(f"fetch_relation_data FINISHED {hash(relation)}")
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.call_from_thread(self.set_data, data)

    @work(exclusive=False)
    def add_data_to_table(self, table: ResultsTable, data: List[Tuple]) -> Worker:
        log(f"add_data_to_table {len(data)}")
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.call_from_thread(table.add_rows, data)
        return worker

    @work(exclusive=True)
    def update_schema_data(self) -> None:
        log("update_schema_data")
        data: DATABASES = []
        databases = self.connection.execute("pragma show_databases").fetchall()
        for (database,) in databases:
            schemas = self.connection.execute(
                "select schema_name "
                "from information_schema.schemata "
                "where "
                "    catalog_name = ? "
                "    and schema_name not in ('pg_catalog', 'information_schema') "
                "order by 1",
                [database],
            ).fetchall()
            schemas_data: SCHEMAS = []
            for (schema,) in schemas:
                tables = self.connection.execute(
                    "select table_name, table_type "
                    "from information_schema.tables "
                    "where "
                    "    table_catalog = ? "
                    "    and table_schema = ? "
                    "order by 1",
                    [database, schema],
                ).fetchall()
                tables_data: TABLES = []
                for table, type in tables:
                    columns = self.connection.execute(
                        "select column_name, data_type "
                        "from information_schema.columns "
                        "where "
                        "    table_catalog = ? "
                        "    and table_schema = ? "
                        "    and table_name = ? "
                        "order by 1",
                        [database, schema, table],
                    ).fetchall()
                    tables_data.append((table, type, columns))
                schemas_data.append((schema, tables_data))
            data.append((database, schemas_data))
        schema_viewer = self.query_one(SchemaViewer)
        worker = get_current_worker()
        if not worker.is_cancelled:
            self.call_from_thread(schema_viewer.update_tree, data)


if __name__ == "__main__":
    app = Harlequin([Path("f1.db")])
    app.run()
