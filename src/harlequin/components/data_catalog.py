from dataclasses import dataclass
from typing import Generic, List, Set, Tuple, Union

from duckdb import DuckDBPyConnection
from textual.binding import Binding
from textual.events import Click
from textual.message import Message
from textual.widgets import Tree
from textual.widgets._tree import EventTreeDataType, TreeNode

from harlequin.duck_ops import Catalog, get_column_label, get_relation_label


@dataclass
class CatalogItem:
    qualified_identifier: str
    query_name: str


class DataCatalog(Tree[CatalogItem]):
    BINDINGS = [
        Binding(
            "ctrl+enter",
            "submit",
            "Insert Name",
            key_display="CTRL+ENTER / CTRL+J",
            show=True,
        ),
        Binding("ctrl+j", "submit", "Insert Name", show=False),
    ]

    BORDER_TITLE = "Data Catalog"

    class NodeSubmitted(Generic[EventTreeDataType], Message):
        def __init__(self, node: TreeNode[EventTreeDataType]) -> None:
            self.node: TreeNode[EventTreeDataType] = node
            super().__init__()

    def __init__(
        self,
        connection: DuckDBPyConnection,
        type_color: str = "#888888",
        data: Union[CatalogItem, None] = None,
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa: A002
        classes: Union[str, None] = None,
        disabled: bool = False,
    ) -> None:
        self.connection = connection
        self.type_color = type_color
        super().__init__(
            "Root", data, name=name, id=id, classes=classes, disabled=disabled
        )
        self.double_click: Union[int, None] = None

    def update_tree(self, catalog: Catalog) -> None:
        tree_state = self._get_node_states(self.root)
        expanded_nodes: Set[str] = set(tree_state[0])
        # todo: tree's select_node() not working
        # unless the tree is modified, the selection will stay
        # in the same place
        # selected_node = tree_state[1]
        self.clear()
        if catalog:
            for database in catalog:
                database_identifier = f'"{database[0]}"'
                database_node = self.root.add(
                    database[0],
                    data=CatalogItem(database_identifier, database_identifier),
                    expand=database_identifier in expanded_nodes,
                )
                for schema in database[1]:
                    schema_identifier = f'{database_identifier}."{schema[0]}"'
                    schema_node = database_node.add(
                        schema[0],
                        data=CatalogItem(schema_identifier, schema_identifier),
                        expand=schema_identifier in expanded_nodes,
                    )
                    for table in schema[1]:
                        table_identifier = f'{schema_identifier}."{table[0]}"'
                        table_node = schema_node.add(
                            label=get_relation_label(
                                rel_name=table[0],
                                rel_type=table[1],
                                type_color=self.type_color,
                            ),
                            data=CatalogItem(table_identifier, table_identifier),
                            expand=table_identifier in expanded_nodes,
                        )
                        for col in table[2]:
                            col_name = f'"{col[0]}"'
                            col_identifier = f"{table_identifier}.{col_name}"
                            table_node.add_leaf(
                                label=get_column_label(
                                    col_name=col[0],
                                    col_type=col[1],
                                    type_color=self.type_color,
                                ),
                                data=CatalogItem(col_identifier, col_name),
                            )

    def on_mount(self) -> None:
        self.show_root = False
        self.guide_depth = 3
        self.root.expand()

    async def on_click(self, event: Click) -> None:
        """
        For whatver reason, it doesn't seem possible to override the super class's
        _on_click event. Instead, here we just handle the parts relevant
        to double clicking, and to prevent nodes from collapsing after double-clicking,
        we collapse them, since the _on_click event will go after this and toggle
        their state.
        """
        meta = event.style.meta
        click_line: Union[int, None] = meta.get("line", None)
        if (
            self.double_click is not None
            and click_line is not None
            and self.double_click == click_line
        ):
            node = self.get_node_at_line(click_line)
            if node is not None:
                self.post_message(self.NodeSubmitted(node=node))
                node.collapse()
        else:
            self.double_click = click_line
            self.set_timer(
                delay=0.5, callback=self._clear_double_click, name="double_click_timer"
            )

    def action_submit(self) -> None:
        if isinstance(self.cursor_line, int) and self.cursor_line > -1:
            node = self.get_node_at_line(self.cursor_line)
            if node is not None:
                self.post_message(self.NodeSubmitted(node=node))

    def _clear_double_click(self) -> None:
        self.double_click = None

    @classmethod
    def _get_node_states(
        cls, node: TreeNode[CatalogItem]
    ) -> Tuple[List[str], Union[str, None]]:
        expanded_nodes = []
        selected_node = None
        if node.is_expanded and node.data is not None:
            expanded_nodes.append(node.data.qualified_identifier)
        if node._selected and node.data is not None:
            selected_node = node.data.qualified_identifier
        for child in node.children:
            expanded_children, selected_child = cls._get_node_states(child)
            expanded_nodes.extend(expanded_children)
            selected_node = selected_child or selected_node
        return expanded_nodes, selected_node
