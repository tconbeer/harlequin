"""Create, rename, and delete one file or folder, then list that directory only.

Listing a directory reads its immediate children. An unexpanded subdirectory
is not walked.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from textual.widgets import DirectoryTree
from textual.widgets._directory_tree import DirEntry
from textual.widgets._tree import TreeNode
from textual.worker import WorkerCancelled, WorkerFailed


class FileOpError(Exception):
    """A create, rename, or delete that the user can be told about."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def clean_entry_name(raw: str) -> str | None:
    """One path component, or None when `raw` is empty or has a separator."""
    name = raw.strip()
    if not name or name in {".", ".."}:
        return None
    if os.sep in name or "/" in name or "\\" in name:
        return None
    return name


def names_matching(names: Iterable[str], query: str) -> list[str]:
    """Names that contain `query`, in their original order.

    An empty query keeps every name. The match is case-insensitive and
    literal: brackets and other punctuation are characters, not a pattern.
    """
    needle = query.casefold()
    if not needle:
        return list(names)
    return [name for name in names if needle in name.casefold()]


def is_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def is_filesystem_root(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return True
    return resolved.parent == resolved


def mutation_refused(path: Path, *, listing_root: Path | None) -> bool:
    """True for the filesystem root, and for a browser's own root directory."""
    if is_filesystem_root(path):
        return True
    if listing_root is None:
        return False
    try:
        return path.expanduser().resolve() == listing_root.expanduser().resolve()
    except OSError:
        return True


def path_at_cursor(tree: DirectoryTree) -> Path | None:
    """The path of the cursor node, when that node is a directory entry."""
    node = tree.cursor_node
    if node is None or not isinstance(node.data, DirEntry):
        return None
    return node.data.path


def directory_at_cursor(tree: DirectoryTree) -> Path | None:
    """The directory at the cursor, the parent of a file there, or the only mount."""
    path = path_at_cursor(tree)
    if path is not None:
        if is_directory(path):
            return path
        parent = path.parent
        if is_directory(parent):
            return parent
        return None
    mounts = getattr(tree, "mounts", None)
    if isinstance(mounts, list) and len(mounts) == 1 and isinstance(mounts[0], Path):
        only = mounts[0]
        if is_directory(only):
            return only
    return None


def _failure(action: str, name: str, exc: OSError) -> FileOpError:
    detail = exc.strerror or exc.__class__.__name__
    return FileOpError(f"Could not {action} {name}: {detail}")


def create_file(directory: Path, name: str) -> Path:
    """Create an empty file. An existing file or folder is left untouched."""
    path = directory / name
    try:
        with path.open("xb"):
            pass
    except FileExistsError:
        raise FileOpError("That name already exists.") from None
    except OSError as exc:
        raise _failure("create", name, exc) from None
    return path


def create_folder(directory: Path, name: str) -> Path:
    """Create an empty folder. An existing file or folder is left untouched."""
    path = directory / name
    try:
        path.mkdir()
    except FileExistsError:
        raise FileOpError("That name already exists.") from None
    except OSError as exc:
        raise _failure("create", name, exc) from None
    return path


def rename_entry(path: Path, name: str) -> Path:
    """Rename `path` to `name` in the same directory. Does not replace a name."""
    if is_filesystem_root(path):
        raise FileOpError("Choose a file or folder in this list.")
    dest = path.parent / name
    try:
        taken = dest.exists() or dest.is_symlink()
    except OSError as exc:
        raise _failure("rename", path.name, exc) from None
    if taken:
        raise FileOpError("That name already exists.")
    try:
        path.rename(dest)
    except OSError as exc:
        raise _failure("rename", path.name, exc) from None
    return dest


def delete_entry(path: Path) -> None:
    """Remove a file, a symlink, or a folder and what is inside the folder.

    A symlink is removed and the target is left in place.
    """
    if is_filesystem_root(path):
        raise FileOpError("Choose a file or folder in this list.")
    try:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        raise _failure("delete", path.name, exc) from None


def find_node(tree: DirectoryTree, directory: Path) -> TreeNode[DirEntry] | None:
    """The loaded node for `directory`. Does not list anything new."""
    try:
        target = directory.expanduser().resolve()
    except OSError:
        return None
    pending: list[TreeNode[DirEntry]] = [tree.root]
    while pending:
        node = pending.pop()
        entry = node.data
        if isinstance(entry, DirEntry):
            try:
                if entry.path.expanduser().resolve() == target:
                    return node
            except OSError:
                pass
        pending.extend(node.children)
    return None


async def relist_one_level(
    tree: DirectoryTree, directory: Path, select_name: str | None
) -> None:
    """Replace the children of `directory` with one listing, when it is shown."""
    node = find_node(tree, directory)
    if node is None or not isinstance(node.data, DirEntry):
        return
    # Marked first so the expand below does not queue the same directory.
    node.data.loaded = True
    async with tree.lock:
        try:
            content = await tree._load_directory(node).wait()
        except (WorkerCancelled, WorkerFailed):
            node.expand()
            return
        if content:
            tree._populate_node(node, content)
        else:
            node.remove_children()
            node.expand()
        target: TreeNode[DirEntry] = node
        if select_name:
            for child in node.children:
                entry = child.data
                if isinstance(entry, DirEntry) and entry.path.name == select_name:
                    target = child
                    break
        _ = tree._tree_lines
        tree.move_cursor(target, animate=False)
