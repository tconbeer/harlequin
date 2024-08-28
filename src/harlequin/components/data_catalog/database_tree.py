from __future__ import annotations

from asyncio import Queue
from typing import List, Set, Tuple, Union

from textual.widgets._tree import TreeNode

from harlequin.catalog import Catalog, CatalogItem, InteractiveCatalogItem
from harlequin.components.data_catalog.tree import HarlequinTree
from harlequin.messages import WidgetMounted


class DatabaseTree(HarlequinTree[CatalogItem], inherit_bindings=False):
    def __init__(
        self,
        type_color: str = "#888888",
        data: CatalogItem | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        self._load_queue: Queue[TreeNode[CatalogItem]] = Queue()
        self.type_color = type_color
        super().__init__(
            "Root", data, name=name, id=id, classes=classes, disabled=disabled
        )

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
        self.loading = False

    def on_mount(self) -> None:
        self.loading = True
        self.show_root = False
        self.guide_depth = 3
        self.root.expand()
        self.post_message(WidgetMounted(widget=self))

    def _build_item_label(self, label: str, type_label: str) -> str:
        return f"{label} [{self.type_color}]{type_label}[/]" if type_label else label

    def _build_subtree(
        self,
        items: List[CatalogItem],
        parent: TreeNode[CatalogItem],
        expanded_nodes: Set[str],
    ) -> None:
        for item in items:
            if isinstance(item, InteractiveCatalogItem):
                item.children = list(item.fetch_children())
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
