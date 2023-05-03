from typing import Any

from duckdb import DuckDBPyConnection
from rich.text import TextType
from textual.widgets import Tree

COLS = list[tuple[str, str]]
TABLES = list[tuple[str, str, COLS]]
SCHEMAS = list[tuple[str, TABLES]]


class SchemaViewer(Tree):
    table_type_mapping = {
        "BASE TABLE": "t",
        "LOCAL TEMPORARY": "tmp",
        "VIEW": "v",
    }
    column_type_mapping = {
        "BIGINT": "##",
        "BIT": "010",
        "BOOLEAN": "t/f",
        "BLOB": "0b",
        "DATE": "d",
        "DOUBLE": "#.#",
        "DECIMAL": "#.#",
        "HUGEINT": "###",
        "INTEGER": "#",
        "INTERVAL": "|-|",
        "REAL": "#.#",
        "SMALLINT": "#",
        "TIME": "t",
        "TIMESTAMP": "ts",
        "TIMESTAMP WITH TIME ZONE": "ttz",
        "TINYINT": "#",
        "UBIGINT": "u##",
        "UINTEGER": "u#",
        "USMALLINT": "u#",
        "UTINYINT": "u#",
        "UUID": "uid",
        "VARCHAR": "s",
        "LIST": "[]",
        "STRUCT": "{}",
        "MAP": "{}",
    }

    def __init__(
        self,
        label: TextType,
        connection: DuckDBPyConnection,
        data: Any | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        self.connection = connection
        super().__init__(
            label, data, name=name, id=id, classes=classes, disabled=disabled
        )

    def on_mount(self) -> None:
        self.border_title = "Schema"
        self.root.expand()

    def update_tree(self, data: SCHEMAS) -> None:
        if data:
            for schema in data:
                schema_node = self.root.add(schema[0])
                for table in schema[1]:
                    short_table_type = self.table_type_mapping.get(table[1], "?")
                    table_node = schema_node.add(
                        f"{table[0]} [#888888]{short_table_type}[/]"
                    )
                    for col in table[2]:
                        short_col_type = self.column_type_mapping.get(
                            col[1].split("(")[0], "?"
                        )
                        table_node.add_leaf(f"{col[0]} [#888888]{short_col_type}[/]")
