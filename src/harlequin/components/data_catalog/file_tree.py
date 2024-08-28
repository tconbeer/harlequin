from __future__ import annotations

from typing import ClassVar

from textual.widgets import (
    DirectoryTree,
)

from harlequin.components.data_catalog.tree import HarlequinTree as HarlequinTree
from harlequin.messages import WidgetMounted


class FileTree(HarlequinTree, DirectoryTree, inherit_bindings=False):
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "directory-tree--extension",
        "directory-tree--file",
        "directory-tree--folder",
        "directory-tree--hidden",
    }

    def on_mount(self) -> None:
        self.guide_depth = 3
        self.root.expand()
        self.post_message(WidgetMounted(widget=self))
