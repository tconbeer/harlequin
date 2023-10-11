from typing import Generic, List, Set, Tuple, Union

from textual.binding import Binding
from textual.events import Click
from textual.message import Message
from textual.widgets import Tree
from textual.widgets._tree import EventTreeDataType, TreeNode

from harlequin.catalog import Catalog, CatalogItem


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
        type_color: str = "#888888",
        data: Union[CatalogItem, None] = None,
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa: A002
        classes: Union[str, None] = None,
        disabled: bool = False,
    ) -> None:
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
        if catalog.items:
            self._build_subtree(catalog.items, self.root, expanded_nodes)

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

    def _build_item_label(self, label: str, type_label: str) -> str:
        return f"{label} [{self.type_color}]{type_label}[/]" if type_label else label

    def _build_subtree(
        self,
        items: List[CatalogItem],
        parent: TreeNode[CatalogItem],
        expanded_nodes: Set[str],
    ) -> None:
        for item in items:
            if item.children:
                new_node = parent.add(
                    label=self._build_item_label(item.label, item.type_label),
                    data=item,
                    expand=item.qualified_identifier in expanded_nodes,
                )
                self._build_subtree(item.children, new_node, expanded_nodes)
            else:
                parent.add_leaf(
                    label=self._build_item_label(item.label, item.type_label), data=item
                )

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
