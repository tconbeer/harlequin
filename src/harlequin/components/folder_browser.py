"""A one-level directory browser for save, open folder, and open file.

The tree lists the directory it is rooted at, and lists a subdirectory only
after that subdirectory is expanded. Moving up lists the parent, once.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.widgets import Button, DirectoryTree, Label, Static, Tree
from textual.widgets._directory_tree import DirEntry
from textual.widgets._tree import TreeNode
from textual.worker import WorkerCancelled, WorkerFailed

from harlequin.components.data_catalog.tree import HarlequinTree
from harlequin.components.modal import HarlequinModal
from harlequin.messages import WidgetMounted


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _root_and_start(start: str | Path) -> tuple[Path, Path]:
    """The parent of `start`, and `start` itself as a directory.

    Rooting at the parent lists the start directory's siblings in one
    listing. The start directory is the only child that is listed next.
    The filesystem root stays the root, because it has no parent.
    """
    try:
        resolved = Path(start).expanduser().resolve()
    except OSError:
        resolved = Path.cwd()
    if _is_file(resolved):
        resolved = resolved.parent
    parent = resolved.parent
    if parent == resolved:
        return resolved, resolved
    return parent, resolved


def _child_named(node: TreeNode[DirEntry], name: str) -> TreeNode[DirEntry] | None:
    for child in node.children:
        entry = child.data
        if isinstance(entry, DirEntry) and entry.path.name == name:
            return child
    return None


class FolderBrowser(HarlequinTree, DirectoryTree, inherit_bindings=False):
    """Lists one directory level. Expanding a folder lists that folder only."""

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "directory-tree--extension",
        "directory-tree--file",
        "directory-tree--folder",
        "directory-tree--hidden",
    }
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("enter", "select_cursor", show=False),
        Binding("space", "toggle_node", show=False),
    ]

    def __init__(
        self,
        start: str | Path,
        *,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        root, resolved = _root_and_start(start)
        self._start = resolved
        # DirectoryTree assigns `path` in __init__. That watcher is queued and
        # runs after mount, which would reload the parent over the cursor
        # on_mount places on the start directory.
        self._ignore_path_watch = True
        super().__init__(
            root,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )

    @property
    def start(self) -> Path:
        """The directory the browser opened on."""
        return self._start

    async def watch_path(self) -> None:
        if self._ignore_path_watch:
            self._ignore_path_watch = False
            return
        if not self.is_mounted:
            return
        await super().watch_path()
        if isinstance(self.root.data, DirEntry):
            self.post_message(Tree.NodeHighlighted(self.root))

    async def on_mount(self) -> None:
        self.guide_depth = 3
        await self._list_directory(self.root)
        await self._reveal(self._start)
        # reload() is what normally starts the loader that serves later expands.
        self._loader()
        self.post_message(WidgetMounted(widget=self))

    async def _list_directory(self, node: TreeNode[DirEntry]) -> None:
        """List `node` itself. A subdirectory's children stay unloaded."""
        entry = node.data
        if not isinstance(entry, DirEntry) or entry.loaded:
            return
        # Marked before the listing returns, so the expand that follows does
        # not queue the same directory again.
        entry.loaded = True
        try:
            content = await self._load_directory(node).wait()
        except (WorkerCancelled, WorkerFailed):
            node.expand()
            return
        if content:
            self._populate_node(node, content)
        else:
            node.expand()

    async def _reveal(self, target: Path) -> None:
        """List each directory from the root down to `target` and select it."""
        root_entry = self.root.data
        if not isinstance(root_entry, DirEntry):
            return
        try:
            relative = target.resolve().relative_to(root_entry.path.resolve())
        except (OSError, ValueError):
            return
        node: TreeNode[DirEntry] = self.root
        for part in relative.parts:
            child = _child_named(node, part)
            if child is None:
                break
            node = child
            child_entry = child.data
            if isinstance(child_entry, DirEntry) and _is_dir(child_entry.path):
                await self._list_directory(child)
        # Lines have to exist before the cursor can sit on `node`.
        _ = self._tree_lines
        self.move_cursor(node, animate=False)

    def action_submit(self) -> None:
        """A catalog insert. This browser only chooses a path."""
        return

    @on(HarlequinTree.NodeSubmitted)
    def keep_the_query(self, message: HarlequinTree.NodeSubmitted) -> None:
        """Double-click posts this. It must not change the query text."""
        message.stop()

    def action_browse_up(self) -> None:
        """List the parent of the current root. One level, and only when asked."""
        try:
            current = Path(self.path).expanduser().resolve()
        except OSError:
            return
        parent = current.parent
        if parent == current:
            return
        # A watcher queued from __init__ must not swallow this move.
        self._ignore_path_watch = False
        self.path = parent

    def path_at_cursor(self) -> Path | None:
        """The path of the cursor node, when it has one."""
        node = self.cursor_node
        if node is None or not isinstance(node.data, DirEntry):
            return None
        return node.data.path

    def cursor_directory(self) -> Path | None:
        """The directory at the cursor, or the parent of a file there."""
        path = self.path_at_cursor()
        if path is None:
            return self._start if _is_dir(self._start) else None
        if _is_dir(path):
            return path
        parent = path.parent
        if _is_dir(parent):
            return parent
        return None


class OpenFolderScreen(HarlequinModal[Path]):
    """Browse to a directory and dismiss with it.

    Enter expands a directory and does not mount it. A file does not dismiss
    the screen. Dismisses with None when the user cancels.
    """

    def __init__(
        self,
        start: Path,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.file_tree = FolderBrowser(start, id="open_folder_tree")

    def compose(self) -> ComposeResult:
        with Vertical(id="open_folder_outer"):
            yield Static(
                "Browse to a folder to add. A file here is not a mount.",
                id="open_folder_prompt",
            )
            yield self.file_tree
            yield Label("", id="open_folder_destination")
            yield Label("", id="open_folder_error")
            with Horizontal(id="open_folder_button_row"):
                yield Button(label="Cancel", id="open_folder_cancel")
                yield Button(label="Up", id="open_folder_up")
                yield Button(
                    label="Open Folder",
                    variant="primary",
                    id="open_folder_open",
                )

    def on_mount(self) -> None:
        self.query_one("#open_folder_outer").border_title = "Open Folder"
        self.file_tree.focus()
        self._show_destination()

    def _show_destination(self) -> None:
        path = self.file_tree.path_at_cursor()
        shown = str(path) if path is not None else ""
        self.query_one("#open_folder_destination", Label).update(
            Content.from_text(shown, markup=False)
        )

    def _show_error(self, message: str) -> None:
        self.query_one("#open_folder_error", Label).update(message)

    @on(DirectoryTree.FileSelected, "#open_folder_tree")
    def file_is_not_a_mount(self, message: DirectoryTree.FileSelected) -> None:
        """A file stays in the editor and does not become a Files root."""
        message.stop()
        self._show_error("Choose a folder.")

    @on(Tree.NodeHighlighted, "#open_folder_tree")
    def destination_follows_the_cursor(self, event: Tree.NodeHighlighted) -> None:
        event.stop()
        self._show_error("")
        self._show_destination()

    @on(Button.Pressed, "#open_folder_up")
    def browse_up(self) -> None:
        self.file_tree.action_browse_up()

    @on(Button.Pressed, "#open_folder_cancel")
    def cancel_from_button(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#open_folder_open")
    def open_from_button(self) -> None:
        path = self.file_tree.path_at_cursor()
        if path is None or not _is_dir(path):
            self._show_error("Choose a folder.")
            return
        self.dismiss(path)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss()


class OpenFileScreen(HarlequinModal[Path]):
    """Browse to a file and dismiss with it.

    Enter on a directory only expands or collapses it. Dismisses with None
    when the user cancels.
    """

    def __init__(
        self,
        start: Path,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.file_tree = FolderBrowser(start, id="open_file_tree")

    def compose(self) -> ComposeResult:
        with Vertical(id="open_file_outer"):
            yield Static(
                "Browse to a text file. A folder only opens in this list.",
                id="open_file_prompt",
            )
            yield self.file_tree
            yield Label("", id="open_file_destination")
            yield Label("", id="open_file_error")
            with Horizontal(id="open_file_button_row"):
                yield Button(label="Cancel", id="open_file_cancel")
                yield Button(label="Up", id="open_file_up")
                yield Button(label="Open File", variant="primary", id="open_file_open")

    def on_mount(self) -> None:
        self.query_one("#open_file_outer").border_title = "Open File"
        self.file_tree.focus()
        self._show_destination()

    def _show_destination(self) -> None:
        path = self.file_tree.path_at_cursor()
        shown = str(path) if path is not None else ""
        self.query_one("#open_file_destination", Label).update(
            Content.from_text(shown, markup=False)
        )

    def _show_error(self, message: str) -> None:
        self.query_one("#open_file_error", Label).update(message)

    @on(DirectoryTree.FileSelected, "#open_file_tree")
    def open_the_file(self, message: DirectoryTree.FileSelected) -> None:
        message.stop()
        self.dismiss(message.path)

    @on(Tree.NodeHighlighted, "#open_file_tree")
    def destination_follows_the_cursor(self, event: Tree.NodeHighlighted) -> None:
        event.stop()
        self._show_error("")
        self._show_destination()

    @on(Button.Pressed, "#open_file_up")
    def browse_up(self) -> None:
        self.file_tree.action_browse_up()

    @on(Button.Pressed, "#open_file_cancel")
    def cancel_from_button(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#open_file_open")
    def open_from_button(self) -> None:
        path = self.file_tree.path_at_cursor()
        if path is None or _is_dir(path):
            self._show_error("Choose a file.")
            return
        self.dismiss(path)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss()
