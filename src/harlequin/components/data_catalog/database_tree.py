from __future__ import annotations

import asyncio
from asyncio import PriorityQueue
from typing import TYPE_CHECKING, Generator, Iterable, TypeVar

from rich.style import Style
from rich.text import Text, TextType
from textual import events, work
from textual.await_complete import AwaitComplete
from textual.reactive import var
from textual.timer import Timer
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

DEMAND_PRIORITY = 0
"""Priority for a node the user expanded; jumps ahead of all speculative work."""

PREFETCH_PRIORITY = 100
"""Priority for a node we load speculatively, because it's on (or near) screen."""

PREFETCH_MARGIN = 20
"""Lines above and below the viewport to warm, so a short scroll finds data ready."""

POPULATE_CHUNK_SIZE = 250
"""TreeNodes to add per event-loop slice, so a wide node can't stall rendering."""

PREFETCH_DEBOUNCE = 0.1
"""Seconds to coalesce viewport scans; scrolling fires them in bursts."""

LOADING_ITEM = CatalogItem(
    qualified_identifier="__loading__",
    query_name="",
    label="",
    type_label="",
)
"""Sentinel data for the placeholder shown under a node while its children load."""


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
            tuple[int, int, TreeNode[InteractiveCatalogItem]]
        ] = PriorityQueue()
        """
        _load_queue is a priority queue, ordered by a priority int, then by an
        ever-increasing sequence number, which makes the queue FIFO within a
        priority and ensures the TreeNodes are never compared (they do not
        implement cmp operators).
        """
        self._queue_seq = 0
        self._queued_priority: dict[int, int] = {}
        """The priority each pending node was queued at, keyed by id(node)."""
        self._loading: set[int] = set()
        """ids of nodes whose fetch_children() call is in flight."""
        self._prefetch_timer: Timer | None = None
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
        self._schedule_prefetch_scan()
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
        self._schedule_prefetch_scan()
        self.loading = False

    def _add_to_load_queue(
        self, node: TreeNode[InteractiveCatalogItem], priority: int = PREFETCH_PRIORITY
    ) -> None:
        """Add the given node to the load priority queue.

        A node already queued at an equal or better priority is left alone; one
        queued at a worse priority is re-queued, and the stale entry is dropped
        when it surfaces. This keeps a node the user expands from being fetched
        (and re-rendered) twice.

        Args:
            node: The node to add to the load queue.
            priority: An order for this node to be loaded; lowest first.
        """
        if node.data is None or node.data.loaded:
            return
        key = id(node)
        if key in self._loading:
            return
        queued_at = self._queued_priority.get(key)
        if queued_at is not None and queued_at <= priority:
            return
        self._queued_priority[key] = priority
        self._queue_seq += 1
        self._load_queue.put_nowait((priority, self._queue_seq, node))

    def _prefetch_window(self) -> tuple[int, int]:
        """The inclusive range of tree lines worth loading speculatively."""
        top = int(self.scroll_offset.y)
        return max(0, top - PREFETCH_MARGIN), top + self.size.height + PREFETCH_MARGIN

    def _in_prefetch_window(self, node: TreeNode[CatalogItem]) -> bool:
        line = node.line
        if line < 0:  # node is not displayed (an ancestor is collapsed)
            return False
        first, last = self._prefetch_window()
        return first <= line <= last

    def _schedule_prefetch_scan(self) -> None:
        """Queue a viewport scan, coalescing the bursts that scrolling produces."""
        if self._prefetch_timer is not None:
            self._prefetch_timer.stop()
        self._prefetch_timer = self.set_timer(
            PREFETCH_DEBOUNCE, self._queue_nodes_in_view
        )

    def _queue_nodes_in_view(self) -> None:
        """Speculatively load the unloaded nodes on (or just off) screen.

        Bounding prefetch by the viewport rather than by the tree's shape is what
        keeps this affordable on a large remote database: a catalog that fits on
        screen is loaded in full, exactly as before, while a catalog with
        thousands of objects costs a screenful of fetches no matter how big it
        gets.
        """
        self._prefetch_timer = None
        first, last = self._prefetch_window()
        lines = self._tree_lines
        for line_no in range(first, min(last + 1, len(lines))):
            node = lines[line_no].node
            if isinstance(node.data, InteractiveCatalogItem) and not node.data.loaded:
                self._add_to_load_queue(node)  # type: ignore[arg-type]

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self._schedule_prefetch_scan()

    def _on_resize(self, event: events.Resize) -> None:
        super()._on_resize(event)
        self._schedule_prefetch_scan()

    def reload(self) -> AwaitComplete:
        """Reload the `DirectoryTree` contents.

        Returns:
            An optionally awaitable that ensures the tree has finished reloading.
        """
        # Orphan the old queue...
        self._load_queue = PriorityQueue()
        self._queued_priority.clear()
        self._loading.clear()
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
                    await self._populate_node(reopening, content)
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

    async def _populate_node(
        self, node: TreeNode[TCatalogItem_co], content: Iterable[TCatalogItem_co]
    ) -> None:
        """Populate the given tree node with the given catalog items.

        Adding TreeNodes is main-thread work, and a schema can hold thousands of
        relations, so the children are added a chunk at a time and the event loop
        is released in between. The lock is taken per chunk rather than for the
        whole run so the tree can rebuild and render the rows added so far.

        Args:
            node: The Tree node to populate.
            content: The CatalogItems to populate the node with.
        """
        items = list(content)
        async with self.lock:
            node.remove_children()
        for offset in range(0, len(items), POPULATE_CHUNK_SIZE):
            async with self.lock:
                for item in items[offset : offset + POPULATE_CHUNK_SIZE]:
                    node.add(
                        self._build_item_label(item.label, item.type_label),
                        data=item,
                        allow_expand=bool(item.children)
                        or not getattr(item, "loaded", True),
                    )
            await asyncio.sleep(0)

    def _show_loading_placeholder(self, node: TreeNode[CatalogItem]) -> None:
        """Give an expanded node something to show while its children are fetched.

        Without this the node expands to nothing at all, which is what makes a
        slow catalog look like a hung one.
        """
        if self._has_placeholder(node):
            return
        type_label_style = self.get_component_rich_style("harlequin-tree--type-label")
        node.remove_children()
        node.add(
            Text("loading…", style=Style(color=type_label_style.color, italic=True)),
            data=LOADING_ITEM,
            allow_expand=False,
        )

    @staticmethod
    def _has_placeholder(node: TreeNode[CatalogItem]) -> bool:
        return len(node.children) == 1 and node.children[0].data is LOADING_ITEM

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

    @work(
        name="_database_tree_background_loader",
        exclusive=True,
        group="database_tree_loaders",
    )
    async def _loader(self) -> None:
        """Background loading queue processor."""
        worker = get_current_worker()
        # Bind the queue once: reload() replaces self._load_queue, and calling
        # task_done() on the replacement -- which has no outstanding get() -- is
        # what raised "task_done() called too many times". exclusive=True also
        # retires this worker when reload() starts its successor.
        queue = self._load_queue
        while not worker.is_cancelled:
            # Get the next node that needs loading off the queue. Note that
            # this blocks if the queue is empty.
            priority, _, node = await queue.get()
            key = id(node)
            try:
                if self._queued_priority.get(key) != priority:
                    # a stale entry: this node was re-queued at a better priority
                    # (or has since been loaded), so it is handled elsewhere.
                    continue
                del self._queued_priority[key]
                if priority > DEMAND_PRIORITY and not self._in_prefetch_window(
                    node  # type: ignore[arg-type]
                ):
                    # scrolled or collapsed out of view before we got to it, so
                    # the speculation no longer pays for itself.
                    continue
                self._loading.add(key)
                try:
                    # Spin up a short-lived thread that will load the children of
                    # the catalog item associated with that node. The tree lock is
                    # deliberately not held here: an adapter's fetch_children() can
                    # take seconds against a remote database, and holding the lock
                    # across it would block the tree's own rendering and input.
                    content = await self._load_children(node).wait()
                except WorkerCancelled:
                    # The worker was cancelled, that would suggest we're all
                    # done here and we should get out of the loader in general.
                    break
                except WorkerFailed:
                    # This particular worker failed to start. We don't know the
                    # reason so let's no-op that (for now anyway).
                    continue
                finally:
                    self._loading.discard(key)
                # _load_children may have cleared allow_expand, and setting that
                # only bumps the node's update counter -- nothing repaints. Without
                # this, a node we found to be empty keeps a phantom expand arrow.
                self._invalidate()
                # Only build TreeNodes the user can actually reach. A collapsed
                # node keeps its children on `node.data`, and they are rendered
                # if and when it is expanded. Empty content still has to be
                # populated, to clear the placeholder off a node with no children.
                if node.is_expanded or self._has_placeholder(node):  # type: ignore[arg-type]
                    await self._populate_node(node, content)
                    self._schedule_prefetch_scan()
            finally:
                # Mark this iteration as done, on the queue this item came from.
                queue.task_done()

    async def _on_tree_node_expanded(
        self, event: Tree.NodeExpanded[CatalogItem]
    ) -> None:
        event.stop()
        node = event.node
        if node.data is None:
            return
        if isinstance(node.data, InteractiveCatalogItem) and not node.data.loaded:
            # if this node isn't loaded yet, add it to the front of the queue
            self._show_loading_placeholder(node)
            self._add_to_load_queue(node, priority=DEMAND_PRIORITY)  # type: ignore[arg-type]
        elif node.data.children and (not node.children or self._has_placeholder(node)):
            await self._populate_node(node, content=node.data.children)
        self._schedule_prefetch_scan()

    def _on_tree_node_collapsed(self, event: Tree.NodeCollapsed[CatalogItem]) -> None:
        # the lines below shifted up; re-scan so we prefetch what's on screen now
        self._schedule_prefetch_scan()
