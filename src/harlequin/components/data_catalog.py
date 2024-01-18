from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Generic, List, Set, Tuple, Union

from rich.text import TextType
from textual import on
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.events import Click
from textual.message import Message
from textual.widgets import (
    DirectoryTree,
    TabbedContent,
    TabPane,
    Tabs,
    Tree,
)
from textual.widgets._directory_tree import DirEntry
from textual.widgets._tree import EventTreeDataType, TreeNode

from harlequin.catalog import Catalog, CatalogItem


class DataCatalog(TabbedContent, can_focus=True):
    BORDER_TITLE = "Data Catalog"

    BINDINGS = [
        Binding("j", "switch_tab(-1)", "Previous Tab", show=False),
        Binding("k", "switch_tab(1)", "Next Tab", show=False),
    ]

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
                return ""

    def __init__(
        self,
        *titles: TextType,
        initial: str = "",
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa: A002
        classes: Union[str, None] = None,
        disabled: bool = False,
        show_files: Path | None = None,
        type_color: str = "#888888",
    ):
        super().__init__(
            *titles,
            initial=initial,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self.show_files = show_files
        self.type_color = type_color

    def on_mount(self) -> None:
        self.database_tree = DatabaseTree(type_color=self.type_color)
        self.add_pane(TabPane("Databases", self.database_tree))
        if self.show_files is not None:
            self.file_tree: FileTree | None = FileTree(path=self.show_files)
            self.add_pane(TabPane("Files", self.file_tree))
        else:
            self.file_tree = None
            self.add_class("hide-tabs")
        self.query_one(Tabs).can_focus = False

    def on_focus(self) -> None:
        if self.active:
            try:
                active_widget = self.query_one(f"#{self.active}").children[0]
            except NoMatches:
                self.database_tree.focus()
            else:
                active_widget.focus()

    @on(TabbedContent.TabActivated)
    def focus_on_widget_in_active_pane(self) -> None:
        self.focus()

    def update_tree(self, catalog: Catalog) -> None:
        self.database_tree.update_tree(catalog)
        if self.file_tree is not None:
            self.file_tree.reload()

    def action_switch_tab(self, offset: int) -> None:
        if not self.active:
            return
        if self.tab_count == 1:
            return
        tab_number = int(self.active.split("-")[1])
        unsafe_tab_number = tab_number + offset
        if unsafe_tab_number < 1:
            new_tab_number = self.tab_count
        elif unsafe_tab_number > self.tab_count:
            new_tab_number = 1
        else:
            new_tab_number = unsafe_tab_number
        self.active = f"tab-{new_tab_number}"
        self.focus()


class SubmitMixin(Tree):
    # TODO: ADD COPY

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

    double_click: int | None = None

    async def on_click(self, event: Click) -> None:
        meta = event.style.meta
        click_line: Union[int, None] = meta.get("line", None)
        self.log(meta, click_line, self.double_click)
        if (
            self.double_click is not None
            and click_line is not None
            and self.double_click == click_line
        ):
            event.prevent_default()
            node = self.get_node_at_line(click_line)
            if node is not None:
                self.post_message(DataCatalog.NodeSubmitted(node=node))
                node.expand()
        else:
            self.double_click = click_line
            self.set_timer(
                delay=0.5, callback=self._clear_double_click, name="double_click_timer"
            )

    def _clear_double_click(self) -> None:
        self.double_click = None

    def action_submit(self) -> None:
        if isinstance(self.cursor_line, int) and self.cursor_line > -1:
            node = self.get_node_at_line(self.cursor_line)
            if node is not None:
                self.post_message(DataCatalog.NodeSubmitted(node=node))


class DatabaseTree(SubmitMixin, Tree[CatalogItem]):
    def __init__(
        self,
        type_color: str = "#888888",
        data: CatalogItem | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
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


class FileTree(SubmitMixin, DirectoryTree):
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "directory-tree--extension",
        "directory-tree--file",
        "directory-tree--folder",
        "directory-tree--hidden",
    }

    def on_mount(self) -> None:
        self.guide_depth = 3
