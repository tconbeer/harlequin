from __future__ import annotations

from typing import ClassVar, Generic, TypeVar, Union

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
    double_click: int | None = None

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

    async def on_click(self, event: Click) -> None:
        meta = event.style.meta
        click_line: Union[int, None] = meta.get("line", None)
        if event.button == 1:  # left button click
            self.post_message(self.HideContextMenu())
            if (
                self.double_click is not None
                and click_line is not None
                and self.double_click == click_line
            ):
                event.prevent_default()
                node = self.get_node_at_line(click_line)
                if node is not None:
                    self.post_message(self.NodeSubmitted(node=node))
                    node.expand()
            else:
                self.double_click = click_line
                self.set_timer(
                    delay=0.5,
                    callback=self._clear_double_click,
                    name="double_click_timer",
                )
        elif event.button == 3 and click_line is not None:  # right click
            node = self.get_node_at_line(click_line)
            if node is not None and isinstance(node.data, CatalogItem):
                self.post_message(self.ShowContextMenu(node=node))

    def _clear_double_click(self) -> None:
        self.double_click = None

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
