from typing import List, Set, Tuple, Union

from duckdb import DuckDBPyConnection
from rich.text import TextType
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from harlequin.duck_ops import Catalog
from harlequin.tui.utils import short_type


class SchemaViewer(Tree[Union[str, None]]):
    table_type_mapping = {
        "BASE TABLE": "t",
        "LOCAL TEMPORARY": "tmp",
        "VIEW": "v",
    }

    def __init__(
        self,
        label: TextType,
        connection: DuckDBPyConnection,
        data: Union[str, None] = None,
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa: A002
        classes: Union[str, None] = None,
        disabled: bool = False,
    ) -> None:
        self.connection = connection
        self.label = label
        super().__init__(
            label, data, name=name, id=id, classes=classes, disabled=disabled
        )

    def on_mount(self) -> None:
        self.border_title = self.label
        self.show_root = False
        self.guide_depth = 3
        self.root.expand()

    def update_tree(self, catalog: Catalog) -> None:
        tree_state = self.get_node_states(self.root)
        expanded_nodes: Set[str] = set(tree_state[0])
        # todo: tree's select_node() not working
        # unless the tree is modified, the selection will stay
        # in the same place
        # selected_node = tree_state[1]
        self.clear()
        if catalog:
            for database in catalog:
                database_node = self.root.add(
                    database[0],
                    data=database[0],
                    expand=database[0] in expanded_nodes,
                )
                for schema in database[1]:
                    schema_node = database_node.add(
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
                            table_node.add_leaf(
                                f"{col[0]} [#888888]{short_type(col[1])}[/]",
                                data=col_identifier,
                            )

    @classmethod
    def get_node_states(
        cls, node: TreeNode[Union[str, None]]
    ) -> Tuple[List[str], Union[str, None]]:
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
