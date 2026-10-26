"""A one-level directory browser for save, open folder, and open file.

The tree lists the directory it is rooted at, and lists a subdirectory only
after that subdirectory is expanded. Moving up lists the parent, once.
The name field creates, renames, or deletes that one directory's entries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Button, DirectoryTree, Input, Label, Static, Tree
from textual.widgets._directory_tree import DirEntry
from textual.widgets._tree import TreeNode
from textual.worker import WorkerCancelled, WorkerFailed

from harlequin.components.confirm_modal import ConfirmModal
from harlequin.components.data_catalog.tree import HarlequinTree
from harlequin.components.file_ops import (
    FileOpError,
    clean_entry_name,
    create_file,
    create_folder,
    delete_entry,
    is_directory,
    is_file,
    mutation_refused,
    path_at_cursor,
    relist_one_level,
    rename_entry,
)
from harlequin.components.listing_search import FilterableTree, ListingSearchInput
from harlequin.components.modal import HarlequinModal
from harlequin.messages import WidgetMounted


def _is_dir(path: Path) -> bool:
    return is_directory(path)


def _is_file(path: Path) -> bool:
    return is_file(path)


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


class FolderBrowser(FilterableTree, inherit_bindings=False):
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
        self.clear_search()
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


def _destination_text(
    tree: FolderBrowser,
    selected: Path | None,
    typed: str,
    *,
    follow_selection: bool,
) -> str:
    """The path the name field is pointing at.

    Open dialogs show the cursor entry when the name still matches it, so a
    folder is not shown as that folder plus its own name. Save always joins
    the typed name onto the folder.
    """
    if follow_selection and selected is not None and typed == selected.name:
        return str(selected)
    directory = tree.cursor_directory()
    if directory is None:
        return "" if selected is None else str(selected)
    if typed:
        return str(directory / typed)
    return str(directory)


def _refused(tree: FolderBrowser, path: Path) -> bool:
    return mutation_refused(path, listing_root=Path(tree.path))


class DialogOps:
    """Create, rename, and delete from a browser dialog's name field."""

    def __init__(
        self,
        app: App[Any],
        tree: FolderBrowser,
        name: Input,
        error: Label,
        destination: Label,
        *,
        follow_selection: bool,
    ) -> None:
        self.app = app
        self.tree = tree
        self.name = name
        self.error = error
        self.destination = destination
        self.follow_selection = follow_selection

    def show_error(self, message: str) -> None:
        self.error.update(Content.from_text(message, markup=False))

    def show_destination(self) -> None:
        selected = path_at_cursor(self.tree)
        shown = _destination_text(
            self.tree,
            selected,
            self.name.value.strip(),
            follow_selection=self.follow_selection,
        )
        self.destination.update(Content.from_text(shown, markup=False))

    def sync_name(self) -> None:
        """Show the cursor entry's name. Save leaves the typed name alone."""
        selected = path_at_cursor(self.tree)
        if selected is not None:
            self.name.value = selected.name

    def chosen_for_open(self, *, folder: bool) -> Path | None:
        """The file or folder the name field and the cursor agree to open."""
        noun = "folder" if folder else "file"
        selected = path_at_cursor(self.tree)
        raw = self.name.value.strip()
        if selected is not None and raw in {"", selected.name}:
            ok = _is_dir(selected) if folder else _is_file(selected)
            if ok:
                return selected
            self.show_error(f"Choose a {noun}.")
            return None
        typed = clean_entry_name(raw)
        directory = self.tree.cursor_directory()
        if typed is None or directory is None:
            self.show_error(f"Choose a {noun}.")
            return None
        candidate = directory / typed
        ok = _is_dir(candidate) if folder else _is_file(candidate)
        if ok:
            return candidate
        self.show_error(f"Choose a {noun}.")
        return None

    async def create(self, *, folder: bool) -> None:
        directory = self.tree.cursor_directory()
        if directory is None:
            self.show_error("Choose a folder.")
            return
        noun = "folder" if folder else "file"
        typed = clean_entry_name(self.name.value)
        if typed is None:
            self.show_error(f"Enter a {noun} name.")
            return
        try:
            if folder:
                created = create_folder(directory, typed)
            else:
                created = create_file(directory, typed)
        except FileOpError as exc:
            self.show_error(exc.message)
            return
        await relist_one_level(self.tree, directory, created.name)
        await self._refresh_catalog_directory(directory, created.name)
        self.show_error("")
        self.show_destination()

    async def rename(self) -> None:
        source = path_at_cursor(self.tree)
        if source is None or _refused(self.tree, source):
            self.show_error("Choose a file or folder in this list.")
            return
        typed = clean_entry_name(self.name.value)
        if typed is None or typed == source.name:
            self.show_error("Enter a new name.")
            return
        try:
            dest = rename_entry(source, typed)
        except FileOpError as exc:
            self.show_error(exc.message)
            return
        self._retarget_start(source, dest)
        self._note_renamed(source, dest)
        await relist_one_level(self.tree, source.parent, dest.name)
        await self._note_catalog(source, dest)
        self.show_error("")
        self.show_destination()

    def ask_delete(self) -> None:
        source = path_at_cursor(self.tree)
        if source is None or _refused(self.tree, source):
            self.show_error("Choose a file or folder in this list.")
            return
        if _is_dir(source):
            prompt = f"Delete folder {source} and everything inside it?"
        else:
            prompt = f"Delete {source}?"

        def decided(confirmed: bool | None) -> None:
            if confirmed:
                self.app.run_worker(self._delete(source))

        self.app.push_screen(
            ConfirmModal(prompt=prompt, confirm_label="Delete", cancel_label="Cancel"),
            decided,
        )

    async def _delete(self, source: Path) -> None:
        if _refused(self.tree, source):
            self.show_error("Choose a file or folder in this list.")
            return
        try:
            delete_entry(source)
        except FileOpError as exc:
            self.show_error(exc.message)
            return
        self._note_deleted(source)
        await relist_one_level(self.tree, source.parent, None)
        await self._note_catalog(source, None)
        self.show_error("")
        self.show_destination()
        self.app.notify(f"Deleted {source}", markup=False)

    def _buffers(self) -> Any:
        return getattr(self.app, "editor_collection", None)

    def _note_renamed(self, source: Path, dest: Path) -> None:
        note = getattr(self._buffers(), "note_path_renamed", None)
        if note is not None:
            note(source, dest)

    def _note_deleted(self, source: Path) -> None:
        note = getattr(self._buffers(), "note_path_deleted", None)
        if note is not None:
            note(source)

    def _retarget_start(self, source: Path, dest: Path) -> None:
        try:
            old = source.expanduser().resolve()
            new = dest.expanduser().resolve()
            start = self.tree.start.expanduser().resolve()
        except OSError:
            return
        if start == old:
            self.tree._start = new
        elif old in start.parents:
            self.tree._start = new / start.relative_to(old)

    async def _refresh_catalog_directory(
        self, directory: Path, select_name: str
    ) -> None:
        catalog = getattr(self.app, "data_catalog", None)
        refresh = getattr(catalog, "refresh_listed_directory", None)
        if refresh is not None:
            await refresh(directory, select_name)

    async def _note_catalog(self, source: Path, dest: Path | None) -> None:
        catalog = getattr(self.app, "data_catalog", None)
        note = getattr(catalog, "note_filesystem_change", None)
        if note is not None:
            await note(source, dest)


def dialog_ops(
    screen: Widget, tree: FolderBrowser, prefix: str, *, follow_selection: bool
) -> DialogOps:
    """The name, error, and path labels of a save or open dialog."""
    return DialogOps(
        screen.app,
        tree,
        screen.query_one(f"#{prefix}_name", Input),
        screen.query_one(f"#{prefix}_error", Label),
        screen.query_one(f"#{prefix}_destination", Label),
        follow_selection=follow_selection,
    )


class NamePrompt(HarlequinModal[str]):
    """Ask for one file or folder name. Dismisses with None on cancel."""

    def __init__(
        self,
        *,
        title: str,
        prompt: str,
        confirm_label: str,
        initial: str = "",
        reject: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.title_text = title
        self.prompt_text = prompt
        self.confirm_label = confirm_label
        self.initial = initial
        self.reject = reject

    def compose(self) -> ComposeResult:
        with Vertical(id="name_prompt_outer"):
            yield Label(
                Content.from_text(self.prompt_text, markup=False),
                id="name_prompt_prompt",
            )
            yield Input(value=self.initial, placeholder="Name", id="name_prompt_input")
            yield Label("", id="name_prompt_error")
            with Horizontal(id="name_prompt_button_row"):
                yield Button(label="Cancel", id="name_prompt_cancel")
                yield Button(
                    label=self.confirm_label,
                    variant="primary",
                    id="name_prompt_ok",
                )

    def on_mount(self) -> None:
        self.query_one("#name_prompt_outer").border_title = self.title_text
        field = self.query_one("#name_prompt_input", Input)
        field.focus()
        field.select_all()

    def _show_error(self, message: str) -> None:
        self.query_one("#name_prompt_error", Label).update(
            Content.from_text(message, markup=False)
        )

    def submit_name(self) -> None:
        name = clean_entry_name(self.query_one("#name_prompt_input", Input).value)
        if name is None or (self.reject is not None and name == self.reject):
            self._show_error("Enter a new name." if self.reject else "Enter a name.")
            return
        self.dismiss(name)

    @on(Input.Submitted, "#name_prompt_input")
    def submit_from_name(self, event: Input.Submitted) -> None:
        event.stop()
        self.submit_name()

    @on(Button.Pressed, "#name_prompt_ok")
    def submit_from_button(self) -> None:
        self.submit_name()

    @on(Button.Pressed, "#name_prompt_cancel")
    def cancel_from_button(self) -> None:
        self.dismiss()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss()


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

    def _ops(self) -> DialogOps:
        return dialog_ops(self, self.file_tree, "open_folder", follow_selection=True)

    def compose(self) -> ComposeResult:
        with Vertical(id="open_folder_outer"):
            yield Static(
                "Browse to a folder to add. A file here is not a mount.",
                id="open_folder_prompt",
            )
            yield self.file_tree
            yield ListingSearchInput(self.file_tree, id="open_folder_search")
            yield Input(placeholder="Name", id="open_folder_name")
            yield Label("", id="open_folder_destination")
            yield Label("", id="open_folder_error")
            with Horizontal(id="open_folder_ops_row"):
                yield Button(label="New File", id="open_folder_new_file")
                yield Button(label="New Folder", id="open_folder_new_folder")
                yield Button(label="Rename", id="open_folder_rename")
                yield Button(label="Delete", id="open_folder_delete")
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
        self._ops().show_destination()

    @on(DirectoryTree.FileSelected, "#open_folder_tree")
    def file_is_not_a_mount(self, message: DirectoryTree.FileSelected) -> None:
        """A file stays in the editor and does not become a Files root."""
        message.stop()
        self._ops().show_error("Choose a folder.")

    @on(Tree.NodeHighlighted, "#open_folder_tree")
    def destination_follows_the_cursor(self, event: Tree.NodeHighlighted) -> None:
        event.stop()
        ops = self._ops()
        ops.sync_name()
        ops.show_error("")
        ops.show_destination()

    @on(Input.Changed, "#open_folder_name")
    def destination_follows_the_name(self, event: Input.Changed) -> None:
        event.stop()
        ops = self._ops()
        ops.show_error("")
        ops.show_destination()

    @on(Input.Submitted, "#open_folder_name")
    def open_from_name(self, event: Input.Submitted) -> None:
        event.stop()
        self.open_from_button()

    @on(Button.Pressed, "#open_folder_up")
    def browse_up(self) -> None:
        self.file_tree.action_browse_up()

    @on(Button.Pressed, "#open_folder_cancel")
    def cancel_from_button(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#open_folder_new_file")
    async def create_file_from_button(self) -> None:
        await self._ops().create(folder=False)

    @on(Button.Pressed, "#open_folder_new_folder")
    async def create_folder_from_button(self) -> None:
        await self._ops().create(folder=True)

    @on(Button.Pressed, "#open_folder_rename")
    async def rename_from_button(self) -> None:
        await self._ops().rename()

    @on(Button.Pressed, "#open_folder_delete")
    def delete_from_button(self) -> None:
        self._ops().ask_delete()

    @on(Button.Pressed, "#open_folder_open")
    def open_from_button(self) -> None:
        path = self._ops().chosen_for_open(folder=True)
        if path is not None:
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

    def _ops(self) -> DialogOps:
        return dialog_ops(self, self.file_tree, "open_file", follow_selection=True)

    def compose(self) -> ComposeResult:
        with Vertical(id="open_file_outer"):
            yield Static(
                "Browse to a text file. A folder only opens in this list.",
                id="open_file_prompt",
            )
            yield self.file_tree
            yield ListingSearchInput(self.file_tree, id="open_file_search")
            yield Input(placeholder="Name", id="open_file_name")
            yield Label("", id="open_file_destination")
            yield Label("", id="open_file_error")
            with Horizontal(id="open_file_ops_row"):
                yield Button(label="New File", id="open_file_new_file")
                yield Button(label="New Folder", id="open_file_new_folder")
                yield Button(label="Rename", id="open_file_rename")
                yield Button(label="Delete", id="open_file_delete")
            with Horizontal(id="open_file_button_row"):
                yield Button(label="Cancel", id="open_file_cancel")
                yield Button(label="Up", id="open_file_up")
                yield Button(label="Open File", variant="primary", id="open_file_open")

    def on_mount(self) -> None:
        self.query_one("#open_file_outer").border_title = "Open File"
        self.file_tree.focus()
        self._ops().show_destination()

    @on(DirectoryTree.FileSelected, "#open_file_tree")
    def open_the_file(self, message: DirectoryTree.FileSelected) -> None:
        message.stop()
        self.dismiss(message.path)

    @on(Tree.NodeHighlighted, "#open_file_tree")
    def destination_follows_the_cursor(self, event: Tree.NodeHighlighted) -> None:
        event.stop()
        ops = self._ops()
        ops.sync_name()
        ops.show_error("")
        ops.show_destination()

    @on(Input.Changed, "#open_file_name")
    def destination_follows_the_name(self, event: Input.Changed) -> None:
        event.stop()
        ops = self._ops()
        ops.show_error("")
        ops.show_destination()

    @on(Input.Submitted, "#open_file_name")
    def open_from_name(self, event: Input.Submitted) -> None:
        event.stop()
        self.open_from_button()

    @on(Button.Pressed, "#open_file_up")
    def browse_up(self) -> None:
        self.file_tree.action_browse_up()

    @on(Button.Pressed, "#open_file_cancel")
    def cancel_from_button(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#open_file_new_file")
    async def create_file_from_button(self) -> None:
        await self._ops().create(folder=False)

    @on(Button.Pressed, "#open_file_new_folder")
    async def create_folder_from_button(self) -> None:
        await self._ops().create(folder=True)

    @on(Button.Pressed, "#open_file_rename")
    async def rename_from_button(self) -> None:
        await self._ops().rename()

    @on(Button.Pressed, "#open_file_delete")
    def delete_from_button(self) -> None:
        self._ops().ask_delete()

    @on(Button.Pressed, "#open_file_open")
    def open_from_button(self) -> None:
        path = self._ops().chosen_for_open(folder=False)
        if path is not None:
            self.dismiss(path)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss()
