from __future__ import annotations

from asyncio import Queue
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Sequence

from rich.markup import escape
from textual import on
from textual.await_complete import AwaitComplete
from textual.content import ContentType
from textual.css.query import InvalidQueryFormat, NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import (
    OptionList,
    TabbedContent,
    TabPane,
    Tabs,
)
from textual.widgets._directory_tree import DirEntry
from textual.widgets._tree import TreeNode

from harlequin.catalog import Catalog, CatalogItem, InteractiveCatalogItem
from harlequin.catalog_cache import CatalogCache
from harlequin.components.data_catalog.database_tree import DatabaseTree
from harlequin.components.data_catalog.s3_tree import S3Tree as S3Tree
from harlequin.components.data_catalog.tree import HarlequinTree as HarlequinTree
from harlequin.components.file_ops import find_node, relist_one_level
from harlequin.components.listing_search import FilterableTree, ListingSearchInput
from harlequin.messages import WidgetMounted

if TYPE_CHECKING:
    from harlequin.catalog import Interaction
    from harlequin.driver import HarlequinDriver

try:
    import boto3
except ImportError:
    boto3 = None  # type: ignore


def boto3_is_installed() -> bool:
    return boto3 is not None


def missing_boto3_error() -> S3Tree.CatalogError:
    return S3Tree.CatalogError(
        catalog_type="s3",
        error=Exception(
            "Could not load s3 catalog because boto3 is not available.\n\n"
            "Re-install harlequin with the s3 extra, like this:\n"
            "uv tool install harlequin[s3]"
        ),
    )


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
        self.file_search: ListingSearchInput | None = None
        if self.show_files is not None:
            self.file_tree: FileTree | None = FileTree(path=self.show_files)
            self.file_search = ListingSearchInput(self.file_tree, id="file_tree_search")
            self.add_pane(TabPane("Files", self.file_search, self.file_tree))
        else:
            self.file_tree = None

        if self.show_s3 is not None and boto3 is not None:
            self.s3_tree: S3Tree | None = S3Tree(uri=self.show_s3)
            self.add_pane(TabPane("S3", self.s3_tree))
        elif self.show_s3 is not None and boto3 is None:
            self.post_message(missing_boto3_error())
            self.s3_tree = None
        else:
            self.s3_tree = None

        if self.show_files is None and self.show_s3 is None:
            self.add_class("hide-tabs")
        self.tabs = self.query_one(Tabs)
        self.tabs.can_focus = False
        self.post_message(WidgetMounted(widget=self))

    def on_focus(self) -> None:
        """Focus the pane's listing. A hidden search field is not that listing."""
        try:
            pane = self.query_one(f"#{self.active}")
        except (NoMatches, InvalidQueryFormat):
            self.database_tree.focus()
            return
        for child in pane.children:
            if child.focusable:
                child.focus()
                return
        self.database_tree.focus()

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

    @property
    def file_mounts(self) -> list[Path]:
        """Directories currently shown in the Files tab, in attach order."""
        if self.file_tree is None:
            return []
        return list(self.file_tree.mounts)

    async def show_file_tree(self, path: Path) -> None:
        """Add `path` to the Files tab, leaving directories already mounted.

        The tab is created the first time. A path that is already mounted
        stays where it is.
        """
        self.show_files = path
        if self.file_tree is None:
            self.file_tree = FileTree(path=path)
            self.file_search = ListingSearchInput(self.file_tree, id="file_tree_search")
            # Files sits between Databases and S3
            s3_pane = self.s3_tree.parent if self.s3_tree is not None else None
            assert s3_pane is None or isinstance(s3_pane, TabPane)
            await self.add_pane(
                TabPane("Files", self.file_search, self.file_tree), before=s3_pane
            )
        else:
            await self.file_tree.attach(path)
        self._activate_pane_of(self.file_tree)

    async def close_current_folder(self) -> None:
        """Unmount the directory under the Files cursor.

        Other mounted directories stay. Query buffers are left alone, including
        a buffer that was opened from the directory being closed. The last
        directory removes the Files tab.
        """
        tree = self.file_tree
        if tree is None:
            return
        mount = tree.mount_at_cursor()
        if mount is None:
            return
        remaining = [path for path in tree.mounts if path != mount]
        await self._apply_mounts(remaining)

    async def _apply_mounts(self, mounts: list[Path]) -> None:
        """Show `mounts`, or remove the Files tab when none remain."""
        tree = self.file_tree
        if tree is None:
            return
        if not mounts:
            pane = tree.parent
            self.file_tree = None
            self.file_search = None
            self.show_files = None
            if isinstance(pane, TabPane) and pane.id is not None:
                await self.remove_pane(pane.id)
            if self.show_s3 is None:
                self.add_class("hide-tabs")
            return
        tree.mounts = mounts
        self.show_files = mounts[-1]
        await tree.reload()

    async def refresh_listed_directory(
        self, directory: Path, select_name: str | None
    ) -> None:
        """List `directory` again when it is already on screen. One level."""
        tree = self.file_tree
        if tree is None or find_node(tree, directory) is None:
            return
        await relist_one_level(tree, directory, select_name)

    async def note_filesystem_change(self, source: Path, dest: Path | None) -> None:
        """Refresh after a rename or delete.

        A mount that moved is reloaded. Anything else relists only its parent.
        `dest` is None when `source` was deleted.
        """
        tree = self.file_tree
        if tree is None:
            return
        try:
            old = source.expanduser().resolve()
        except OSError:
            return
        new: Path | None = None
        if dest is not None:
            try:
                new = dest.expanduser().resolve()
            except OSError:
                return
        mounts: list[Path] = []
        touched = False
        for mount in tree.mounts:
            if mount == old or old in mount.parents:
                touched = True
                if new is None:
                    continue
                if mount == old:
                    mounts.append(new)
                else:
                    mounts.append(new / mount.relative_to(old))
            else:
                mounts.append(mount)
        if touched:
            await self._apply_mounts(mounts)
            return
        parent = new.parent if new is not None else old.parent
        if find_node(tree, parent) is None:
            return
        await relist_one_level(tree, parent, None if new is None else new.name)

    async def show_s3_tree(self, uri: str) -> S3Tree | None:
        """Show `uri` in the S3 tab, adding the tab if there is none.

        Returns the tree it replaced, whose listing the caller may cache. A
        listing still in flight for the old location lands on the removed tree.
        """
        self.show_s3 = uri
        replaced_tree = self.s3_tree
        self.s3_tree = S3Tree(uri=uri)
        if replaced_tree is None:
            await self.add_pane(TabPane("S3", self.s3_tree))
        else:
            s3_pane = replaced_tree.parent
            assert isinstance(s3_pane, TabPane)
            await replaced_tree.remove()
            await s3_pane.mount(self.s3_tree)
        self._activate_pane_of(self.s3_tree)
        return replaced_tree

    def _activate_pane_of(self, tree: Widget) -> None:
        self.remove_class("hide-tabs")
        if tree.parent is not None and tree.parent.id is not None:
            self.active = tree.parent.id

    def update_database_tree(self, catalog: Catalog) -> None:
        self.database_tree.catalog = catalog

    def update_file_tree(self) -> None:
        if self.file_tree is not None:
            self.file_tree.reload()

    def update_s3_tree(self) -> None:
        if self.s3_tree is not None:
            self.s3_tree.reload()

    def load_s3_tree_from_cache(self, cache: CatalogCache) -> None:
        # a tree with data already has the live listing, which is newer
        if (
            self.show_s3 is None
            or self.s3_tree is None
            or self.s3_tree.catalog_data is not None
        ):
            return
        cache_data = cache.get_s3(self.s3_tree.cache_key)
        if cache_data is None:
            return
        self.s3_tree.build_tree(data=cache_data)

    def action_switch_tab(self, offset: int) -> None:
        if not self.active or self.tab_count == 1:
            return
        # by position: a tab added at run time can sit before an older one
        if offset < 0:
            self.tabs.action_previous_tab()
        else:
            self.tabs.action_next_tab()

    def action_focus_results_viewer(self) -> None:
        if hasattr(self.app, "action_focus_results_viewer"):
            self.app.action_focus_results_viewer()

    def action_focus_query_editor(self) -> None:
        if hasattr(self.app, "action_focus_query_editor"):
            self.app.action_focus_query_editor()


class FileTree(FilterableTree, inherit_bindings=False):
    """A directory listing. One mount is that directory. Several are its roots."""

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "directory-tree--extension",
        "directory-tree--file",
        "directory-tree--folder",
        "directory-tree--hidden",
    }

    def __init__(
        self,
        path: str | Path,
        *,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        self.mounts: list[Path] = [Path(path).expanduser().resolve()]
        super().__init__(
            path,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )

    def on_mount(self) -> None:
        self.guide_depth = 3
        self.root.expand()
        self.post_message(WidgetMounted(widget=self))

    async def attach(self, path: Path) -> None:
        """Show `path` as another root. A directory already shown stays put."""
        resolved = path.expanduser().resolve()
        if resolved in self.mounts:
            return
        if not resolved.is_dir():
            return
        self.mounts.append(resolved)
        await self.reload()

    def mount_at_cursor(self) -> Path | None:
        """The mounted directory that contains the cursor, if there is one."""
        if not self.mounts:
            return None
        node = self.cursor_node
        if node is None or node.data is None:
            return self.mounts[0] if len(self.mounts) == 1 else None
        try:
            chosen = Path(node.data.path).expanduser().resolve()
        except OSError:
            return None
        for mount in self.mounts:
            if chosen == mount or mount in chosen.parents:
                return mount
        return None

    def reload(self) -> AwaitComplete:
        """Reload every mounted directory. One mount keeps a single root."""
        if len(self.mounts) <= 1:
            if len(self.mounts) == 1:
                only = self.mounts[0]
                root_path = None if self.root.data is None else self.root.data.path
                if root_path is None or Path(root_path).expanduser().resolve() != only:
                    self.reset_node(self.root, str(only), DirEntry(only))
            return super().reload()
        return AwaitComplete(self._reload_mounts())

    async def watch_path(self) -> None:
        """A new `path` is another mount when several directories are open.

        A single mount still repopulates from that path, as a DirectoryTree does.
        """
        self.clear_search()
        resolved = Path(self.path).expanduser().resolve()
        if resolved not in self.mounts:
            self.mounts.append(resolved)
        if self.mounts == [resolved]:
            await super().watch_path()
            return
        await self._reload_mounts()

    async def _reload_mounts(self) -> None:
        """Show one node per mount under a Files root, and list each mount."""
        self._load_queue = Queue()
        names = [mount.name for mount in self.mounts]
        async with self.lock:
            self.clear_node(self.root)
            self.root.data = None
            self.root.label = "Files"
            self.root.allow_expand = True
            for mount in self.mounts:
                label = str(mount) if names.count(mount.name) > 1 else mount.name
                self.root.add(label, data=DirEntry(mount), allow_expand=True)
            self.root.expand()
        for node in list(self.root.children):
            self._add_to_load_queue(node)
        self._loader()
        await self._load_queue.join()
