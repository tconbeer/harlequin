from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Generic, List, Set, Tuple, Union
from urllib.parse import urlsplit

from rich.text import TextType
from textual import on, work
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
from textual.worker import Worker, WorkerState

from harlequin.catalog import Catalog, CatalogItem
from harlequin.catalog_cache import CatalogCache, recursive_dict
from harlequin.messages import WidgetMounted

try:
    import boto3
except ImportError:
    boto3 = None  # type: ignore


class DataCatalog(TabbedContent, can_focus=True):
    BORDER_TITLE = "Data Catalog"

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

    class CatalogError(Message):
        def __init__(self, catalog_type: str, error: BaseException) -> None:
            self.catalog_type = catalog_type
            self.error = error
            super().__init__()

    def __init__(
        self,
        *titles: TextType,
        initial: str = "",
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa: A002
        classes: Union[str, None] = None,
        disabled: bool = False,
        show_files: Path | None = None,
        show_s3: str | None = None,
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
        self.show_s3 = show_s3
        self.type_color = type_color

    def on_mount(self) -> None:
        self.database_tree = DatabaseTree(type_color=self.type_color)
        self.add_pane(TabPane("Databases", self.database_tree))
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
                DataCatalog.CatalogError(
                    catalog_type="s3",
                    error=Exception(
                        "Could not load s3 catalog because boto3 is not available.\n\n"
                        "Re-install harlequin with the s3 extra, like this:\n"
                        "pip install harlequin[s3]"
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
        except NoMatches:
            self.database_tree.focus()
        else:
            active_widget.focus()

    @on(TabbedContent.TabActivated)
    def focus_on_widget_in_active_pane(self) -> None:
        self.focus()

    def update_database_tree(self, catalog: Catalog) -> None:
        self.database_tree.update_tree(catalog)

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


class HarlequinTree(Tree, inherit_bindings=False):

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
        if self.cursor_node is not None:
            self.post_message(DataCatalog.NodeSubmitted(node=self.cursor_node))

    def action_copy(self) -> None:
        if self.cursor_node is not None:
            self.post_message(DataCatalog.NodeCopied(node=self.cursor_node))


class DatabaseTree(HarlequinTree, Tree[CatalogItem], inherit_bindings=False):
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


class S3Tree(HarlequinTree, Tree[str], inherit_bindings=False):
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "directory-tree--extension",
        "directory-tree--file",
        "directory-tree--folder",
        "directory-tree--hidden",
    }

    class DataReady(Message):
        def __init__(self, data: dict) -> None:
            self.data = data
            super().__init__()

    def __init__(
        self,
        uri: str,
        data: CatalogItem | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        self.endpoint_url, self.bucket, self.prefix = self._parse_s3_uri(uri)
        self.catalog_data: dict | None = None
        super().__init__(
            "Root", data, name=name, id=id, classes=classes, disabled=disabled
        )

    render_label = DirectoryTree.render_label

    def on_mount(self) -> None:
        self.guide_depth = 3
        self.show_root = False
        self.root.data = self.endpoint_url or "s3:/"
        self.reload()
        self.post_message(WidgetMounted(widget=self))

    @property
    def cache_key(self) -> tuple[str | None, str | None, str | None]:
        return (self.endpoint_url, self.bucket, self.prefix)

    @on(DataReady)
    def build_tree_from_message_data(self, message: S3Tree.DataReady) -> None:
        self.build_tree(message.data)

    def build_tree(self, data: dict) -> None:
        self.catalog_data = data
        self.clear()
        self._build_subtree(data=data, parent=self.root)
        self.root.expand()
        self.loading = False

    @on(Worker.StateChanged)
    async def handle_worker_state_change(self, message: Worker.StateChanged) -> None:
        if message.state == WorkerState.ERROR:
            await self._handle_worker_error(message)

    async def _handle_worker_error(self, message: Worker.StateChanged) -> None:
        if (
            message.worker.name == "_reload_objects"
            and message.worker.error is not None
        ):
            self.post_message(
                DataCatalog.CatalogError(catalog_type="s3", error=message.worker.error)
            )
            self.loading = False

    @staticmethod
    def _parse_s3_uri(uri: str) -> tuple[str | None, str | None, str | None]:
        """
        Any of these are acceptable:
        my-bucket
        my-bucket/my-prefix
        s3://my-bucket
        s3://my-bucket/my-prefix
        https://my-storage.com/my-bucket/
        https://my-storage.com/my-bucket/my-prefix
        https://my-bucket.s3.amazonaws.com/my-prefix
        https://my-bucket.storage.googleapis.com/my-prefix
        """

        def _is_prefixed_aws_url(netloc: str) -> bool:
            parts = netloc.split(".")
            print(parts)
            if ".".join(parts[1:]) == "s3.amazonaws.com":
                return True
            return False

        def _is_prefixed_gcs_url(netloc: str) -> bool:
            parts = netloc.split(".")
            if ".".join(parts[1:]) == "storage.googleapis.com":
                return True
            return False

        if uri == "all":
            # special keyword so we list all buckets
            return None, None, None

        scheme, netloc, path, *_ = urlsplit(uri)
        path = path.lstrip("/")
        bucket: str | None

        if not scheme:
            assert not netloc
            endpoint_url = None
            bucket = path.split("/")[0]
            prefix = "/".join(path.split("/")[1:])
        elif scheme == "s3":
            endpoint_url = None
            bucket = netloc
            prefix = path
        elif _is_prefixed_aws_url(netloc):
            endpoint_url = None
            bucket = netloc.split(".")[0]
            prefix = path
        elif _is_prefixed_gcs_url(netloc):
            endpoint_url = "https://storage.googleapis.com"
            bucket = netloc.split(".")[0]
            prefix = path
        else:
            endpoint_url = f"{scheme}://{netloc}"
            bucket = path.split("/")[0] or None
            prefix = "/".join(path.split("/")[1:])

        return endpoint_url, bucket, prefix

    def reload(self) -> None:
        self.loading = True
        self._reload_objects()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _reload_objects(self) -> None:
        if boto3 is None:
            return

        data = {}
        s3 = boto3.resource("s3", endpoint_url=self.endpoint_url)
        if self.bucket is None:
            buckets = [b for b in s3.buckets.all()]
        else:
            buckets = [s3.Bucket(self.bucket)]
        for bucket in buckets:
            self.log(f"building tree for {bucket.name}")
            data[bucket.name] = recursive_dict()
            object_gen = (
                bucket.objects.filter(Prefix=self.prefix)
                if self.prefix
                else bucket.objects.all()
            )
            for obj in object_gen:
                self.log(f"inserting {obj.key} into tree {bucket.name}")
                key_parts = obj.key.split("/")
                target = data[bucket.name]
                for part in key_parts:
                    target = target[part]

        self.post_message(S3Tree.DataReady(data=data))

    def _build_subtree(
        self,
        data: dict[str, dict],
        parent: TreeNode[str],
    ) -> None:
        for k in data:
            if data[k]:
                new_node = parent.add(
                    label=k,
                    data=f"{parent.data}/{k}",
                )
                self._build_subtree(data[k], new_node)
            else:
                parent.add_leaf(label=k, data=f"{parent.data}/{k}")
