from rich.text import TextType
from textual.widgets import Tree
from textual.widgets.tree import TreeNode
from textual.app import ComposeResult
from typing import Any
from duckdb import DuckDBPyConnection


class SchemaViewer(Tree):
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
        self._update_tree()
        self.root.expand()

    def _update_tree(self) -> None:
        rows = self.connection.execute(
            "select table_name, table_schema from information_schema.tables"
        ).fetchall()
        if rows:
            schemas = {schema for _, schema in rows}
            schema_nodes: dict[str, TreeNode] = {}
            for schema in schemas:
                node = self.root.add(schema)
                schema_nodes[schema] = node
            for table, schema in rows:
                parent = schema_nodes[schema]
                node = parent.add_leaf(table)
