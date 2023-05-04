from duckdb import DuckDBPyConnection
from rich.text import TextType
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

COLS = list[tuple[str, str]]
TABLES = list[tuple[str, str, COLS]]
SCHEMAS = list[tuple[str, TABLES]]


class SchemaViewer(Tree[str | None]):
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
        data: str | None = None,
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
        tree_state = self.get_node_states(self.root)
        expanded_nodes: set[str] = set(tree_state[0])
        # todo: tree's select_node() not working
        # unless the tree is modified, the selection will stay
        # in the same place
        # selected_node = tree_state[1]
        self.clear()
        if data:
            for schema in data:
                schema_node = self.root.add(
                    schema[0], data=schema[0], expand=schema[0] in expanded_nodes
                )
                for table in schema[1]:
                    short_table_type = self.table_type_mapping.get(table[1], "?")
                    table_identifier = f"{schema[0]}.{table[0]}"
                    table_node = schema_node.add(
                        f"{table[0]} [#888888]{short_table_type}[/]",
                        data=table_identifier,
                        expand=(table_identifier in expanded_nodes),
                    )
                    for col in table[2]:
                        col_identifier = f"{table_identifier}.{col[0]}"
                        short_col_type = self.column_type_mapping.get(
                            col[1].split("(")[0], "?"
                        )
                        table_node.add_leaf(
                            f"{col[0]} [#888888]{short_col_type}[/]",
                            data=col_identifier,
                        )

    @classmethod
    def get_node_states(
        cls, node: TreeNode[str | None]
    ) -> tuple[list[str], str | None]:
        expanded_nodes = []
        selected_node = None
        if node.is_expanded and node.data is not None:
            expanded_nodes.append(node.data)
        if node._selected and node.data is not None:
            selected_node = node.data
        for child in node.children:
            expanded_children, selected_child = cls.get_node_states(child)
            expanded_nodes.extend(expanded_children)
            selected_node = selected_child or selected_node
        return expanded_nodes, selected_node
