from typing import Generic, List, Set, Tuple, Union

from duckdb import DuckDBPyConnection
from rich.text import TextType
from textual.binding import Binding
from textual.events import Click
from textual.message import Message
from textual.widgets import Tree
from textual.widgets._tree import EventTreeDataType, TreeNode

from harlequin.duck_ops import Catalog
from harlequin.tui.utils import short_type


class SchemaViewer(Tree[str]):
    class NodeSubmitted(Generic[EventTreeDataType], Message):
        def __init__(self, node: TreeNode[EventTreeDataType]) -> None:
            self.node: TreeNode[EventTreeDataType] = node
            super().__init__()

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

    table_type_mapping = {
        "BASE TABLE": "t",
        "LOCAL TEMPORARY": "tmp",
        "VIEW": "v",
    }

    def __init__(
        self,
        label: TextType,
        connection: DuckDBPyConnection,
        type_color: str = "#888888",
        data: Union[str, None] = None,
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa: A002
        classes: Union[str, None] = None,
        disabled: bool = False,
    ) -> None:
        self.connection = connection
        self.label = label
        self.type_color = type_color
        super().__init__(
            label, data, name=name, id=id, classes=classes, disabled=disabled
        )
        self.double_click = False

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
                database_identifier = f'"{database[0]}"'
                database_node = self.root.add(
                    database[0],
                    data=database_identifier,
                    expand=database_identifier in expanded_nodes,
                )
                for schema in database[1]:
                    schema_identifier = f'{database_identifier}."{schema[0]}"'
                    schema_node = database_node.add(
                        schema[0],
                        data=schema_identifier,
                        expand=schema_identifier in expanded_nodes,
                    )
                    for table in schema[1]:
                        short_table_type = self.table_type_mapping.get(table[1], "?")
                        table_identifier = f'{schema_identifier}."{table[0]}"'
                        table_node = schema_node.add(
                            f"{table[0]} [{self.type_color}]{short_table_type}[/]",
                            data=table_identifier,
                            expand=table_identifier in expanded_nodes,
                        )
                        for col in table[2]:
                            col_identifier = f'{table_identifier}."{col[0]}"'
                            table_node.add_leaf(
                                f"{col[0]} [{self.type_color}]{short_type(col[1])}[/]",
                                data=col_identifier,
                            )

    @classmethod
    def get_node_states(cls, node: TreeNode[str]) -> Tuple[List[str], Union[str, None]]:
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

    def _clear_double_click(self) -> None:
        self.double_click = False

    async def on_click(self, event: Click) -> None:
        """
        For whatver reason, it doesn't seem possible to override the super class's
        _on_click event. Instead, here we just handle the parts relevant
        to double clicking, and to prevent nodes from collapsing after double-clicking,
        we collapse them, since the _on_click event will go after this and toggle
        their state.
        """
        if self.double_click:
            meta = event.style.meta
            cursor_line = meta.get("line", None)
            if cursor_line is not None:
                node = self.get_node_at_line(cursor_line)
                if node is not None:
                    self.post_message(self.NodeSubmitted(node=node))
                    node.collapse()
        else:
            self.double_click = True
            self.set_timer(
                delay=0.5, callback=self._clear_double_click, name="double_click_timer"
            )

    def action_submit(self) -> None:
        if isinstance(self.cursor_line, int) and self.cursor_line > -1:
            node = self.get_node_at_line(self.cursor_line)
            if node is not None:
                self.post_message(self.NodeSubmitted(node=node))
