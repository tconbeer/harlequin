from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Sequence

from rich.markup import escape
from textual import on
from textual.content import ContentType
from textual.css.query import InvalidQueryFormat, NoMatches
from textual.message import Message
from textual.widgets import (
    DirectoryTree,
    OptionList,
    TabbedContent,
    TabPane,
    Tabs,
)
from textual.widgets._tree import TreeNode

from harlequin.catalog import Catalog, CatalogItem, InteractiveCatalogItem
from harlequin.catalog_cache import CatalogCache
from harlequin.components.data_catalog.database_tree import DatabaseTree
from harlequin.components.data_catalog.s3_tree import S3Tree as S3Tree
from harlequin.components.data_catalog.tree import HarlequinTree as HarlequinTree
from harlequin.messages import WidgetMounted

if TYPE_CHECKING:
    from harlequin.catalog import Interaction
    from harlequin.driver import HarlequinDriver

try:
    import boto3
except ImportError:
    boto3 = None  # type: ignore


def insert_name_at_cursor(item: CatalogItem, driver: HarlequinDriver) -> None:
    driver.insert_text_at_selection(text=item.query_name)


class ContextMenu(OptionList):
    class ExecuteInteraction(Message):
        def __init__(self, interaction: "Interaction", item: CatalogItem) -> None:
            self.interaction = interaction
            self.item = item
            super().__init__()

    DEFAULT_INTERACTIONS: list[tuple[str, "Interaction"]] = [
        ("Insert Name at Cursor", insert_name_at_cursor),
    ]

    def __init__(self) -> None:
        self.interactions: list[tuple[str, "Interaction"]] = self.DEFAULT_INTERACTIONS
        self.item: CatalogItem | None = None
        super().__init__()

    def reload(self, node: TreeNode) -> None:
        self.clear_options()

        if not isinstance(node.data, CatalogItem):
            return

        self.item = node.data

        other_interactions: Sequence[tuple[str, "Interaction"]] = []

        if isinstance(node.data, InteractiveCatalogItem):
            if node.data.INTERACTIONS is not None:
                other_interactions = node.data.INTERACTIONS

        self.interactions = [*self.DEFAULT_INTERACTIONS, *other_interactions]

        for label, _ in self.interactions:
            self.add_option(escape(label))

        assert isinstance(self.parent, TabPane)
        parent_height = self.parent.scrollable_content_region.height
        context_menu_height = self.option_count + 2
        scroll_offset = node.tree.scroll_offset.y
        if (node.line - scroll_offset + context_menu_height) < parent_height:
            self.styles.offset = (0, node.line - scroll_offset + 1)
        else:
            self.styles.offset = (0, node.line - scroll_offset - context_menu_height)

        self.add_class("open")
        self.highlighted = 0
        self.focus()

    @on(OptionList.OptionSelected)
    def execute_interaction(self, event: OptionList.OptionSelected) -> None:
        if event.option_list is not self or self.item is None:
            return
        _, interaction = self.interactions[event.option_index]
        self.post_message(
            self.ExecuteInteraction(interaction=interaction, item=self.item)
        )
        self.remove_class("open")

    def on_blur(self) -> None:
        self.action_hide()

    def action_hide(self) -> None:
        self.remove_class("open")


class DataCatalog(TabbedContent, can_focus=True):
    BORDER_TITLE = "Data Catalog"

    def __init__(
        self,
        *titles: ContentType,
        initial: str = "",
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
        show_files: Path | None = None,
        show_s3: str | None = None,
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
        self.show_s3 = show_s3

    def on_mount(self) -> None:
        self.database_tree = DatabaseTree()
        self.database_context_menu = ContextMenu()
        self.add_pane(
            TabPane("Databases", self.database_tree, self.database_context_menu)
        )
        if self.show_files is not None:
            self.file_tree: FileTree | None = FileTree(path=self.show_files)
            self.add_pane(TabPane("Files", self.file_tree))
        else:
            self.file_tree = None

        if self.show_s3 is not None and boto3 is not None:
            self.s3_tree: S3Tree | None = S3Tree(uri=self.show_s3)
            self.add_pane(TabPane("S3", self.s3_tree))
        elif self.show_s3 is not None and boto3 is None:
            self.post_message(
                S3Tree.CatalogError(
                    catalog_type="s3",
                    error=Exception(
                        "Could not load s3 catalog because boto3 is not available.\n\n"
                        "Re-install harlequin with the s3 extra, like this:\n"
                        "uv tool install harlequin[s3]"
                    ),
                )
            )
            self.s3_tree = None
        else:
            self.s3_tree = None

        if self.show_files is None and self.show_s3 is None:
            self.add_class("hide-tabs")
        self.query_one(Tabs).can_focus = False
        self.post_message(WidgetMounted(widget=self))

    def on_focus(self) -> None:
        try:
            active_widget = self.query_one(f"#{self.active}").children[0]
        except (NoMatches, InvalidQueryFormat):
            self.database_tree.focus()
        else:
            active_widget.focus()

    @on(TabbedContent.TabActivated)
    def focus_on_widget_in_active_pane(self) -> None:
        self.focus()

    @on(HarlequinTree.HideContextMenu)
    def hide_context_menu(self, event: HarlequinTree.HideContextMenu) -> None:
        event.stop()
        self.database_context_menu.remove_class("open")

    @on(HarlequinTree.ShowContextMenu)
    def load_interactions_and_show_context_menu(
        self, event: HarlequinTree.ShowContextMenu
    ) -> None:
        event.stop()
        self.database_context_menu.reload(node=event.node)

    def update_database_tree(self, catalog: Catalog) -> None:
        self.database_tree.catalog = catalog

    def update_file_tree(self) -> None:
        if self.file_tree is not None:
            self.file_tree.reload()

    def update_s3_tree(self) -> None:
        if self.s3_tree is not None:
            self.s3_tree.reload()

    def load_s3_tree_from_cache(self, cache: CatalogCache) -> None:
        if self.show_s3 is None or self.s3_tree is None:
            return
        cache_data = cache.get_s3(self.s3_tree.cache_key)
        if cache_data is None:
            return
        self.s3_tree.build_tree(data=cache_data)

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

    def action_focus_results_viewer(self) -> None:
        if hasattr(self.app, "action_focus_results_viewer"):
            self.app.action_focus_results_viewer()

    def action_focus_query_editor(self) -> None:
        if hasattr(self.app, "action_focus_query_editor"):
            self.app.action_focus_query_editor()


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
