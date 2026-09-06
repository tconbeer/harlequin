from __future__ import annotations

from typing import ClassVar, Generic, TypeVar, Union

from rich.text import Text
from textual.events import Click
from textual.message import Message
from textual.widgets import (
    Tree,
)
from textual.widgets._directory_tree import DirEntry
from textual.widgets._tree import EventTreeDataType, TreeNode

from harlequin.catalog import CatalogItem

TTreeNode = TypeVar("TTreeNode")


class HarlequinTree(Tree[TTreeNode], inherit_bindings=False):
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "harlequin-tree--type-label",
    }

    class CatalogError(Message):
        def __init__(self, catalog_type: str, error: BaseException) -> None:
            self.catalog_type = catalog_type
            self.error = error
            super().__init__()

    class NodeSubmitted(Generic[EventTreeDataType], Message):
        def __init__(self, node: TreeNode[EventTreeDataType]) -> None:
            self.node: TreeNode[EventTreeDataType] = node
            super().__init__()

        @property
        def insert_name(self) -> str:
            if not self.node.data:
                return ""
            elif isinstance(self.node.data, CatalogItem):
                return self.node.data.query_name
            elif isinstance(self.node.data, DirEntry):
                return f"'{self.node.data.path}'"
            else:
                return str(self.node.data)

    class NodeCopied(Generic[EventTreeDataType], Message):
        def __init__(self, node: TreeNode[EventTreeDataType]) -> None:
            self.node: TreeNode[EventTreeDataType] = node
            super().__init__()

        @property
        def copy_name(self) -> str:
            if not self.node.data:
                return ""
            elif isinstance(self.node.data, CatalogItem):
                return self.node.data.query_name
            elif isinstance(self.node.data, DirEntry):
                return str(self.node.data.path)
            else:
                return str(self.node.data)

    class ShowContextMenu(Message):
        def __init__(self, node: TreeNode) -> None:
            self.node = node
            super().__init__()

    class HideContextMenu(Message):
        pass

    def on_focus(self) -> None:
        if self.cursor_line < 0:
            self.cursor_line = 0

    def watch_hover_line(self, previous_hover_line: int, hover_line: int) -> None:
        super().watch_hover_line(previous_hover_line, hover_line)
        self.tooltip = self._tooltip_for_line(hover_line)

    def _tooltip_for_line(self, line_index: int) -> Text | None:
        """The full label of the node on that line, if it doesn't fit in the tree.

        A label that fits gets no tooltip, since the tooltip would only cover
        the item it repeats.
        """
        tree_lines = self._tree_lines
        if not 0 <= line_index < len(tree_lines):
            return None
        line = tree_lines[line_index]
        rendered_width = self.get_label_width(line.node) + line._get_guide_width(
            self.guide_depth, self.show_root
        )
        if rendered_width <= self.size.width:
            return None
        # the Text, not its plain string: the tooltip renders the type label in
        # the same muted color the tree does, and a str would be parsed as
        # console markup -- a list column's type label is literally "[s]".
        label = line.node.label
        return label if isinstance(label, Text) else Text(label)

    async def on_click(self, event: Click) -> None:
        meta = event.style.meta
        click_line: Union[int, None] = meta.get("line", None)
        if event.button == 1:  # left button click
            self.post_message(self.HideContextMenu())
            if event.chain == 2 and click_line is not None:  # double click
                event.prevent_default()
                node = self.get_node_at_line(click_line)
                if node is not None:
                    self.post_message(self.NodeSubmitted(node=node))
                    node.expand()
        elif event.button == 3 and click_line is not None:  # right click
            node = self.get_node_at_line(click_line)
            if node is not None and isinstance(node.data, CatalogItem):
                self.post_message(self.ShowContextMenu(node=node))

    def action_submit(self) -> None:
        if self.cursor_node is not None:
            self.post_message(self.NodeSubmitted(node=self.cursor_node))

    def action_copy(self) -> None:
        if self.cursor_node is not None:
            self.post_message(self.NodeCopied(node=self.cursor_node))

    def action_show_context_menu(self) -> None:
        if self.cursor_node is not None and isinstance(
            self.cursor_node.data, CatalogItem
        ):
            self.post_message(self.ShowContextMenu(node=self.cursor_node))

    def action_hide_context_menu(self) -> None:
        self.post_message(self.HideContextMenu())
