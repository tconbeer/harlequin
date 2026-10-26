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
            # Focus selects the whole value by default. The first character is
            # inserted, then this field is focused, so the next key would
            # replace that character and "ga" would be stored as "a".
            select_on_focus=False,
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
        search.insert_text_at_cursor(typed)
        search.focus()

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

    def _name_matches(self, node: TreeNode[DirEntry]) -> bool:
        """Whether this row's own name contains the query."""
        if not self._name_query:
            return True
        return bool(names_matching([_entry_name(node)], self._name_query))

    def _shown_in_filter(self, node: TreeNode[DirEntry]) -> bool:
        """A visible name stays when it matches, or a loaded child does.

        The cursor often sits on an expanded folder. That folder's children
        are on screen, so a child named gamma stays when the query is "ga"
        even though the folder's own name does not match.
        """
        if self._name_matches(node):
            return True
        return any(self._shown_in_filter(child) for child in node.children)

    def _first_match(self, node: TreeNode[DirEntry]) -> TreeNode[DirEntry] | None:
        """The first loaded row under `node` whose name contains the query."""
        for child in node.children:
            if not self._shown_in_filter(child):
                continue
            if self._name_matches(child):
                return child
            found = self._first_match(child)
            if found is not None:
                return found
        return None

    def _keep_cursor_on_a_shown_row(self) -> None:
        if not self._name_query:
            return
        cursor = self.cursor_node
        if cursor is None or cursor is self.root or self._shown_in_filter(cursor):
            return
        match = self._first_match(self.root)
        if match is not None:
            self.move_cursor(match, animate=False)
        else:
            self.move_cursor(self.root, animate=False)

    def _build(self) -> None:
        if not self._name_query:
            super()._build()
            return
        # Hide names for this draw only. The nodes stay loaded, and no
        # directory is listed to produce the shorter view. A folder stays
        # when a loaded descendant matches, so that name remains on screen.
        saved: list[tuple[TreeNode[DirEntry], list[TreeNode[DirEntry]]]] = []

        def narrow(node: TreeNode[DirEntry]) -> None:
            original = list(node._children)
            shown = [child for child in original if self._shown_in_filter(child)]
            saved.append((node, original))
            node._children[:] = shown
            for child in shown:
                if child._children:
                    narrow(child)

        narrow(self.root)
        try:
            super()._build()
        finally:
            for node, original in reversed(saved):
                node._children[:] = original
