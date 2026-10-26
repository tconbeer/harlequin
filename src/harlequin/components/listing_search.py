"""Type-to-search for the one directory a listing is showing.

The match is a pure comparison of entry names. It does not read another
directory. Arrow keys and enter still move and expand the tree.
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual import events
from textual.binding import Binding, BindingType
from textual.widgets import DirectoryTree, Input
from textual.widgets._directory_tree import DirEntry
from textual.widgets._tree import TreeNode

from harlequin.components.data_catalog.tree import HarlequinTree
from harlequin.components.file_ops import names_matching


def _entry_name(node: TreeNode[DirEntry]) -> str:
    """The filename of a directory entry, or the node's own label."""
    entry = node.data
    if isinstance(entry, DirEntry):
        return entry.path.name
    label = node.label
    plain = getattr(label, "plain", None)
    if isinstance(plain, str):
        return plain
    return str(label)


class ListingSearchInput(Input):
    """The search field for one directory listing.

    Up, down, and enter move the tree. Escape clears the field, then leaves
    it. Other keys edit the query.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "listing_up", show=False, priority=True),
        Binding("down", "listing_down", show=False, priority=True),
        Binding("enter", "listing_enter", show=False, priority=True),
        Binding("escape", "listing_escape", show=False, priority=True),
    ]

    def __init__(
        self,
        tree: FilterableTree,
        *,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        merged = "listing-search" if not classes else f"listing-search {classes}"
        super().__init__(
            placeholder="Search",
            name=name,
            id=id,
            classes=merged,
            disabled=disabled,
        )
        self._tree = tree
        self.display = False
        tree.attach_search(self)

    def allow_focus(self) -> bool:
        """A hidden search field must not take the listing's keys."""
        return self.display and super().allow_focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Narrow the listing to what has been typed. The name field is separate."""
        event.stop()
        self._tree.set_name_query(event.value)

    def action_listing_up(self) -> None:
        self._tree.action_cursor_up()

    def action_listing_down(self) -> None:
        self._tree.action_cursor_down()

    def action_listing_enter(self) -> None:
        """Expand or collapse. This field does not open or save."""
        self._tree.action_select_cursor()

    def action_listing_escape(self) -> None:
        """Clear the query, or put the field away when it is already empty."""
        if self.value:
            self.value = ""
            return
        self.display = False
        self._tree.focus()


class FilterableTree(HarlequinTree, DirectoryTree):
    """A directory tree that can hide the names in the directory on screen."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._name_query = ""
        self._query_node: TreeNode[DirEntry] | None = None
        self._search: ListingSearchInput | None = None
        super().__init__(*args, **kwargs)

    def attach_search(self, search: ListingSearchInput) -> None:
        """The field that shows the query. Typing in the tree focuses it."""
        self._search = search

    def on_key(self, event: events.Key) -> None:
        """A letter starts a search. Space, arrows, and enter stay with the tree."""
        if not self.has_focus:
            return
        typed = event.character
        if typed is None or not typed.isprintable() or event.key == "space":
            return
        search = self._search
        if search is None:
            return
        event.stop()
        event.prevent_default()
        search.display = True
        search.focus()
        search.insert_text_at_cursor(typed)

    def on_directory_tree_directory_selected(
        self, _message: DirectoryTree.DirectorySelected
    ) -> None:
        """Opening or closing a folder shows that directory's own names."""
        self.clear_search()

    def set_name_query(self, query: str) -> None:
        """Show only the entry names in this directory that contain `query`."""
        search = self._search
        if query == self._name_query:
            if not query and search is not None and search.display:
                search.display = False
                if search.has_focus:
                    self.focus()
            return
        self._name_query = query
        self._query_node = self._listing_node()
        self._keep_cursor_on_a_shown_row()
        self._invalidate()
        if search is None or query:
            return
        search.display = False
        if search.has_focus:
            self.focus()

    def clear_search(self) -> None:
        """Show every name in the directory on screen and empty the field."""
        search = self._search
        if search is not None and search.value:
            search.value = ""
            return
        if self._name_query:
            self._name_query = ""
            self._invalidate()
        if search is None:
            return
        if search.display:
            search.display = False
        if search.has_focus:
            self.focus()

    def _listing_node(self) -> TreeNode[DirEntry]:
        """The directory whose immediate children are the list on screen."""
        cursor = self.cursor_node
        if cursor is None or cursor.parent is None:
            return self.root
        return cursor.parent

    def _shown_children(self, node: TreeNode[DirEntry]) -> list[TreeNode[DirEntry]]:
        children = list(node.children)
        if not self._name_query:
            return children
        names = [_entry_name(child) for child in children]
        allowed = set(names_matching(names, self._name_query))
        return [
            child
            for child, name in zip(children, names, strict=True)
            if name in allowed
        ]

    def _keep_cursor_on_a_shown_row(self) -> None:
        if not self._name_query:
            return
        node = self._listing_node()
        shown = self._shown_children(node)
        cursor = self.cursor_node
        if cursor is None or cursor is node or cursor in shown:
            return
        if shown:
            self.move_cursor(shown[0], animate=False)
        else:
            self.move_cursor(node, animate=False)

    def _build(self) -> None:
        node = self._listing_node()
        if (
            self._query_node is not None
            and node is not self._query_node
            and self._name_query
        ):
            self._name_query = ""
            self.call_later(self.clear_search)
        self._query_node = node
        if not self._name_query:
            super()._build()
            return
        # Hide names for this draw only. The nodes stay loaded, and no
        # directory is listed to produce the shorter view.
        original = list(node._children)
        node._children[:] = self._shown_children(node)
        try:
            super()._build()
        finally:
            node._children[:] = original
