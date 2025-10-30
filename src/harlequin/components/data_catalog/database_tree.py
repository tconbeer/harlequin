from __future__ import annotations

from asyncio import PriorityQueue
from contextlib import suppress
from typing import TYPE_CHECKING, Generator, Iterable, TypeVar

from rich.style import Style
from rich.text import Text, TextType
from textual import work
from textual.await_complete import AwaitComplete
from textual.reactive import var
from textual.widgets._tree import Tree, TreeNode
from textual.worker import WorkerCancelled, WorkerFailed, get_current_worker

from harlequin.catalog import (
    Catalog,
    CatalogItem,
    InteractiveCatalogItem,
    NewCatalogItems,
)
from harlequin.components.data_catalog.tree import HarlequinTree
from harlequin.messages import WidgetMounted

if TYPE_CHECKING:
    from typing_extensions import Self


class DatabaseTree(HarlequinTree[CatalogItem], inherit_bindings=False):
    catalog: var[Catalog | None] = var["Catalog | None"](
        None, init=False, always_update=True
    )

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        self._load_queue: PriorityQueue[
            tuple[int, str, int, TreeNode[InteractiveCatalogItem]]
        ] = PriorityQueue()
        """
        _load_queue is a priority queue, ordered by a priority int,
        then the string label of the node, then the id of the node,
        which should prevent any comparisons between TreeNodes, which
        do not implement cmp operators.
        """
        super().__init__(
            label="Root",
            data=CatalogItem(
                qualified_identifier="__root__",
                query_name="",
                label="Root",
                type_label="",
                children=[],
            ),
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )

    def on_mount(self) -> None:
        self.loading = True
        self.show_root = False
        self.guide_depth = 3
        self.root.expand()
        self.post_message(WidgetMounted(widget=self))

    def _build_item_label(self, label: str, type_label: str) -> Text:
        type_label_style = self.get_component_rich_style("harlequin-tree--type-label")
        type_label_fg_style = Style(color=type_label_style.color)
        return Text.assemble(label, " ", (type_label, type_label_fg_style))

    async def watch_catalog(self, catalog: Catalog | None) -> None:
        """Watch for changes to the `catalog` of the database tree.

        If the Catalog is changed the database tree will be repopulated using
        the new value as the root.
        """
        assert isinstance(self.root.data, CatalogItem)
        self.root.data.children = catalog.items if catalog is not None else []
        await self.reload()
        # add the root's children to the prefetch queue
        for child in self.root.children:
            if isinstance(child.data, InteractiveCatalogItem) and not child.data.loaded:
                self._add_to_load_queue(child)  # type: ignore[arg-type]
        self.loading = False

    def _add_to_load_queue(
        self, node: TreeNode[InteractiveCatalogItem], priority: int = 100
    ) -> None:
        """Add the given node to the load priority queue.

        Args:
            node: The node to add to the load queue.
            priority: An order for this node to be loaded; lowest first.
        """
        assert node.data is not None
        if not node.data.loaded:
            with suppress(TypeError):
                # typeError will be raised if this node already exists in the queue,
                # since TreeNodes do not implement cmp operators.
                self._load_queue.put_nowait((priority, str(node.label), id(node), node))

    def reload(self) -> AwaitComplete:
        """Reload the `DirectoryTree` contents.

        Returns:
            An optionally awaitable that ensures the tree has finished reloading.
        """
        # Orphan the old queue...
        self._load_queue = PriorityQueue()
        # ... reset the root node ...
        processed = self.reload_node(self.root)
        # ... and replace the old loader with a new one.
        self._loader()
        return processed

    def clear_node(self, node: TreeNode[CatalogItem]) -> Self:
        """Clear all nodes under the given node.

        Returns:
            The `Tree` instance.
        """
        self._clear_line_cache()
        node.remove_children()
        self._updates += 1
        self.refresh()
        return self

    def reset_node(
        self,
        node: TreeNode[CatalogItem],
        label: TextType,
        data: CatalogItem | None = None,
    ) -> Self:
        """Clear the subtree and reset the given node.

        Args:
            node: The node to reset.
            label: The label for the node.
            data: Optional data for the node.

        Returns:
            The `Tree` instance.
        """
        self.clear_node(node)
        node.label = label
        node.data = data
        return self

    async def _reload(self, node: TreeNode[CatalogItem]) -> None:
        """Reloads the subtree rooted at the given node while preserving state.

        After reloading the subtree, nodes that were expanded and still exist
        will remain expanded and the highlighted node will be preserved, if it
        still exists. If it doesn't, highlighting goes up to the first parent
        directory that still exists.

        Args:
            node: The root of the subtree to reload.
        """
        async with self.lock:
            # Track nodes that were expanded before reloading.
            currently_open: set[str] = set()
            to_check: list[TreeNode[CatalogItem]] = [node]
            while to_check:
                checking = to_check.pop()
                if checking.allow_expand and checking.is_expanded:
                    if checking.data:
                        currently_open.add(checking.data.qualified_identifier)
                    to_check.extend(checking.children)

            # Track node that was highlighted before reloading.
            highlighted_identifier: None | str = None
            if self.cursor_line > -1:
                highlighted_node = self.get_node_at_line(self.cursor_line)
                if highlighted_node is not None and highlighted_node.data is not None:
                    highlighted_identifier = highlighted_node.data.qualified_identifier

            if node.data is not None:
                self.reset_node(
                    node,
                    self._build_item_label(node.data.label, node.data.type_label),
                    node.data,
                )

            # Reopen nodes that were expanded and still exist.
            to_reopen = [node]
            while to_reopen:
                reopening = to_reopen.pop()
                if not reopening.data:
                    continue
                if reopening.allow_expand and (
                    reopening.data.qualified_identifier in currently_open
                    or reopening == node
                ):
                    try:
                        content = await self._load_children(reopening).wait()
                    except (WorkerCancelled, WorkerFailed):
                        continue
                    self._populate_node(reopening, content)
                    to_reopen.extend(reopening.children)
                    reopening.expand()

            if highlighted_identifier is None:
                return

            # Restore the highlighted path and consider the parents as fallbacks.
            looking = [node]

            def parents(qualified_identifier: str) -> Generator[str, None, None]:
                parent = qualified_identifier
                while parent := parent.rpartition(".")[0]:
                    yield parent
                yield "__root__"

            highlight_candidates = set(parents(highlighted_identifier))
            highlight_candidates.add(highlighted_identifier)
            best_found: None | TreeNode[CatalogItem] = None
            while looking:
                checking = looking.pop()
                checking_path = (
                    checking.data.qualified_identifier
                    if checking.data is not None
                    else None
                )
                if checking_path in highlight_candidates:
                    best_found = checking
                    if checking_path == highlighted_identifier:
                        break
                    if checking.allow_expand and checking.is_expanded:
                        looking.extend(checking.children)
            if best_found is not None:
                # We need valid lines. Make sure the tree lines have been computed:
                _ = self._tree_lines
                self.cursor_line = best_found.line

    def reload_node(self, node: TreeNode[CatalogItem]) -> AwaitComplete:
        """Reload the given node's contents.

        The return value may be awaited to ensure the DirectoryTree has reached
        a stable state and is no longer performing any node reloading (of this node
        or any other nodes).

        Args:
            node: The root of the subtree to reload.

        Returns:
            An optionally awaitable that ensures the subtree has finished reloading.
        """
        return AwaitComplete(self._reload(node))

    TCatalogItem_co = TypeVar("TCatalogItem_co", bound=CatalogItem, covariant=True)

    def _populate_node(
        self, node: TreeNode[TCatalogItem_co], content: Iterable[TCatalogItem_co]
    ) -> None:
        """Populate the given tree node with the given directory content.

        Args:
            node: The Tree node to populate.
            content: The collection of `Path` objects to populate the node with.
        """
        node.remove_children()
        for item in content:
            node.add(
                self._build_item_label(item.label, item.type_label),
                data=item,
                allow_expand=bool(item.children) or not getattr(item, "loaded", True),
            )

    @work(thread=True, exit_on_error=False, description="_load_children")
    def _load_children(self, node: TreeNode[CatalogItem]) -> list[CatalogItem]:
        """Load the children for a given node.

        Args:
            node: The node to load the children for.

        Returns:
            The list of entries within the directory associated with the node.
        """
        assert node.data is not None
        if (
            not node.data.children
            and isinstance(node.data, InteractiveCatalogItem)
            and not node.data.loaded
        ):
            try:
                children = list(node.data.fetch_children())
            except BaseException as e:
                self.post_message(self.CatalogError(catalog_type="database", error=e))
                return []
            else:
                node.data.children = children
                self.post_message(NewCatalogItems(parent=node.data, items=children))
            finally:
                node.data.loaded = True
                node.allow_expand = bool(node.data.children)

        return sorted(
            node.data.children,
            key=lambda catalog_item: catalog_item.label,
        )

    @work(name="_database_tree_background_loader")
    async def _loader(self) -> None:
        """Background loading queue processor."""
        worker = get_current_worker()
        while not worker.is_cancelled:
            # Get the next node that needs loading off the queue. Note that
            # this blocks if the queue is empty.
            *_, node = await self._load_queue.get()
            content: list[CatalogItem] = []
            async with self.lock:
                try:
                    # Spin up a short-lived thread that will load the content of
                    # the directory associated with that node.
                    content = await self._load_children(node).wait()
                except WorkerCancelled:
                    # The worker was cancelled, that would suggest we're all
                    # done here and we should get out of the loader in general.
                    break
                except WorkerFailed:
                    # This particular worker failed to start. We don't know the
                    # reason so let's no-op that (for now anyway).
                    pass
                else:
                    # We're still here and we have directory content, get it into
                    # the tree.
                    if content:
                        self._populate_node(node, content)
                finally:
                    # Mark this iteration as done.
                    self._load_queue.task_done()

    async def _on_tree_node_expanded(
        self, event: Tree.NodeExpanded[CatalogItem]
    ) -> None:
        event.stop()
        node = event.node
        if node.data is None:
            return
        if isinstance(node.data, InteractiveCatalogItem) and not node.data.loaded:
            # if this node isn't loaded yet, add it to the front of the queue
            self._add_to_load_queue(node, priority=0)  # type: ignore[arg-type]
        if (
            isinstance(node.data, CatalogItem)
            and node.data.children
            and not node.children
        ):
            self._populate_node(node, content=node.data.children)
        # pre-fetch the node's grandchildren
        for child in node.children:
            if isinstance(child.data, InteractiveCatalogItem):
                self._add_to_load_queue(child)  # type: ignore[arg-type]
