"""Browse to any folder when saving, and open a folder or a file from there."""

from __future__ import annotations

from pathlib import Path
from typing import Type

import pytest
from textual.pilot import Pilot
from textual.widgets import DirectoryTree, Input
from textual.widgets._directory_tree import DirEntry
from textual.widgets._tree import TreeNode

from harlequin import Harlequin
from harlequin.components.confirm_modal import ConfirmModal
from harlequin.components.folder_browser import (
    NamePrompt,
    OpenFileScreen,
    OpenFolderScreen,
)
from harlequin.components.save_query_screen import SaveQueryScreen
from harlequin_duckdb.adapter import DuckDbAdapter
from tests.functional_tests.helpers import wait_for_editor
from tests.functional_tests.test_command_palette import (
    click_confirm_modal,
    run_palette_command,
)
from tests.functional_tests.test_file_mounts import _line_named, _location
from tests.functional_tests.test_file_tree_buffers import _label, activate, write_exact
from tests.waiting import wait_for, wait_for_value

TEXT = "select 7;\n"
OTHER = "select 99;\n"
NOTES = "select 4;\n"


def _entry(node: TreeNode[DirEntry] | None) -> DirEntry | None:
    if node is None or not isinstance(node.data, DirEntry):
        return None
    return node.data


def _child(node: TreeNode[DirEntry], name: str) -> TreeNode[DirEntry] | None:
    for child in node.children:
        entry = _entry(child)
        if entry is not None and entry.path.name == name:
            return child
    return None


def _has_name(node: TreeNode[DirEntry], name: str) -> bool:
    entry = _entry(node)
    if entry is not None and entry.path.name == name:
        return True
    return any(_has_name(child, name) for child in node.children)


def _tree_has_submit(tree: DirectoryTree) -> bool:
    """Ctrl+J on a Harlequin tree is bound to insert-name (`submit`)."""
    bindings = tree._bindings.key_to_bindings.get("ctrl+j", [])
    return any(binding.action == "submit" for binding in bindings)


async def _double_click_line(pilot: Pilot, tree: DirectoryTree, line: int) -> None:
    """Double-click the rendered row `line`, the same cells a user would."""
    await pilot.pause()
    region = tree.region
    found: tuple[int, int] | None = None
    for y in range(region.y, region.y + region.height):
        for x in range(region.x, region.x + region.width):
            meta = tree.app.screen.get_style_at(x, y).meta
            if meta is not None and meta.get("line") == line:
                found = (x - region.x, y - region.y)
                break
        if found is not None:
            break
    assert found is not None
    await pilot.double_click(tree, offset=found)


async def _do_not_insert(
    pilot: Pilot, tree: DirectoryTree, editor_text: str, app: Harlequin
) -> None:
    """Ctrl+J and a double-click must leave the query text alone."""
    tree.focus()
    await wait_for(
        pilot,
        lambda: _tree_has_submit(tree),
        description="insert-name to bind on the browser",
    )
    await pilot.press("ctrl+j")
    await pilot.press("ctrl+enter")
    await pilot.pause()
    editor = app.editor
    assert editor is not None
    assert editor.text == editor_text
    assert tree.cursor_line >= 0
    await _double_click_line(pilot, tree, tree.cursor_line)
    await pilot.pause()
    assert editor.text == editor_text


async def _cursor_on(
    pilot: Pilot, tree: DirectoryTree, name: str
) -> TreeNode[DirEntry]:
    line = await wait_for_value(
        pilot,
        lambda: _line_named(tree, name),
        description=f"{name} in the browser",
    )
    tree.focus()
    tree.move_cursor_to_line(line)
    node = tree.get_node_at_line(line)
    assert node is not None
    return node


@pytest.mark.asyncio
async def test_save_browses_to_an_unmounted_directory(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Save names a file in a folder that is not mounted, and a folder only toggles."""
    shelf = tmp_path / "shelf"
    mounted = shelf / "mounted"
    inner = mounted / "inner"
    parked = tmp_path / "parked"
    for directory in (mounted, inner, parked):
        directory.mkdir(parents=True)
    write_exact(inner / "hidden.sql", "hidden\n")
    notes = parked / "notes.sql"

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=mounted)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        assert _location(app) == "Not a file"
        editor.focus()
        editor.text = TEXT
        await pilot.press("ctrl+s")
        screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, SaveQueryScreen) else None,
            description="the folder-and-name save dialog",
        )
        tree = screen.file_tree
        inner_node = await _cursor_on(pilot, tree, "inner")
        assert _child(inner_node, "hidden.sql") is None
        entry = _entry(inner_node)
        assert entry is not None and entry.loaded is False

        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: (
                inner_node.is_expanded and _child(inner_node, "hidden.sql") is not None
            ),
            description="inner to expand without saving",
        )
        assert not notes.exists()
        assert _location(app) == "Not a file"
        assert isinstance(app.screen, SaveQueryScreen)

        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: not inner_node.is_expanded,
            description="inner to collapse without saving",
        )
        assert not notes.exists()
        assert isinstance(app.screen, SaveQueryScreen)

        await pilot.click("#save_query_up")
        parked_node = await _cursor_on(pilot, tree, parked.name)
        assert _entry(parked_node) is not None
        name_input = screen.query_one("#save_query_name", Input)
        name_input.value = notes.name
        name_input.focus()
        await pilot.press("enter")

        await wait_for(
            pilot,
            lambda: (
                _label(app) == notes.name
                and _location(app) == str(notes.resolve())
                and notes.is_file()
            ),
            description="the tab to bind notes.sql in the unmounted folder",
        )
        assert notes.read_bytes() == editor.text.encode("utf-8")
        assert _location(app) == str(notes.resolve())
        assert app.data_catalog.file_mounts == [mounted.resolve()]
        assert app.editor_collection.tab_count == 1
        assert app._exception is None


@pytest.mark.asyncio
async def test_open_folder_mounts_beside_existing(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Open Folder adds a directory. A file there does not mount or edit."""
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    write_exact(alpha / "orders.sql", TEXT)
    write_exact(alpha / "other.sql", OTHER)
    write_exact(beta / "keep.sql", "keep\n")

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=alpha)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        tree = app.data_catalog.file_tree
        assert tree is not None
        await activate(pilot, tree, "orders.sql")
        await wait_for(
            pilot,
            lambda: editor.text == TEXT and _label(app) == "orders.sql",
            description="orders.sql to open",
        )

        await run_palette_command(pilot, app, "Open Folder")
        screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, OpenFolderScreen) else None,
            description="the open-folder browser",
        )
        await _cursor_on(pilot, screen.file_tree, "other.sql")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, OpenFolderScreen)
        assert app.data_catalog.file_mounts == [alpha.resolve()]
        assert editor.text == TEXT
        assert _label(app) == "orders.sql"
        assert app.editor_collection.tab_count == 1

        await _cursor_on(pilot, screen.file_tree, beta.name)
        await pilot.click("#open_folder_open")
        await wait_for(
            pilot,
            lambda: app.data_catalog.file_mounts == [alpha.resolve(), beta.resolve()],
            description="beta to mount beside alpha",
        )
        assert not isinstance(app.screen, OpenFolderScreen)
        assert editor.text == TEXT
        assert _label(app) == "orders.sql"
        assert app.editor_collection.tab_count == 1
        assert (beta / "keep.sql").read_bytes() == b"keep\n"
        assert app._exception is None


@pytest.mark.asyncio
async def test_open_file_loads_one_text_buffer(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Open File loads one buffer. A folder toggles. A non-text file does not."""
    docs = tmp_path / "docs"
    nested = docs / "nested"
    nested.mkdir(parents=True)
    notes = docs / "notes.sql"
    write_exact(notes, NOTES)
    write_exact(nested / "inner.sql", "inner\n")
    (docs / "blob.bin").write_bytes(b"\x00\x01")

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=docs)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        assert editor.text == ""

        await run_palette_command(pilot, app, "Open File")
        screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, OpenFileScreen) else None,
            description="the open-file browser",
        )
        await _cursor_on(pilot, screen.file_tree, notes.name)
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: editor.text == NOTES and _label(app) == notes.name,
            description="notes.sql to load into one buffer",
        )
        assert _location(app) == str(notes.resolve())
        assert app.editor_collection.tab_count == 1

        await run_palette_command(pilot, app, "Open File")
        screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, OpenFileScreen) else None,
            description="the open-file browser again",
        )
        await _cursor_on(pilot, screen.file_tree, notes.name)
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, OpenFileScreen),
            description="the second open to close",
        )
        assert editor.text == NOTES
        assert _label(app) == notes.name
        assert app.editor_collection.tab_count == 1

        await run_palette_command(pilot, app, "Open File")
        screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, OpenFileScreen) else None,
            description="the open-file browser for a folder",
        )
        folder = await _cursor_on(pilot, screen.file_tree, "nested")
        assert _child(folder, "inner.sql") is None
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: folder.is_expanded and _child(folder, "inner.sql") is not None,
            description="nested to expand",
        )
        assert isinstance(app.screen, OpenFileScreen)
        assert editor.text == NOTES
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: not folder.is_expanded,
            description="nested to collapse",
        )
        assert isinstance(app.screen, OpenFileScreen)
        assert editor.text == NOTES
        assert app.editor_collection.tab_count == 1

        await _cursor_on(pilot, screen.file_tree, "blob.bin")
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, OpenFileScreen),
            description="the non-text file to leave the browser",
        )
        assert editor.text == NOTES
        assert _label(app) == notes.name
        assert app.editor_collection.tab_count == 1
        assert app._exception is None


@pytest.mark.asyncio
async def test_browser_lists_one_directory_level(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """A shown directory's children are listed. An unexpanded subdirectory is not."""
    shown = tmp_path / "shown"
    nested = shown / "nested"
    nested.mkdir(parents=True)
    secret = nested / "secret.sql"
    write_exact(secret, "secret\n")
    for index in range(40):
        write_exact(shown / f"f{index:02d}.sql", "select 1;\n")

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=shown)
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_editor(pilot, app)
        await run_palette_command(pilot, app, "Open Folder")
        screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, OpenFolderScreen) else None,
            description="the browser on the wide directory",
        )
        tree = screen.file_tree
        shown_node = await _cursor_on(pilot, tree, shown.name)
        names = {
            entry.path.name
            for child in shown_node.children
            if (entry := _entry(child)) is not None
        }
        assert "f00.sql" in names
        assert "f39.sql" in names
        assert "nested" in names
        assert len(shown_node.children) == 41
        nested_node = _child(shown_node, "nested")
        assert nested_node is not None
        nested_entry = _entry(nested_node)
        assert nested_entry is not None and nested_entry.loaded is False
        assert not nested_node.children
        assert not _has_name(tree.root, "secret.sql")

        tree.focus()
        tree.move_cursor_to_line(nested_node.line)
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: _child(nested_node, "secret.sql") is not None,
            description="secret.sql to load only after nested expands",
        )
        assert nested_node.is_expanded
        assert _entry(nested_node) is not None
        assert app._exception is None


@pytest.mark.asyncio
async def test_browser_does_not_insert_a_path_into_the_query(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Ctrl+J and a double-click leave the query alone, and save writes those bytes."""
    mounted = tmp_path / "mounted"
    mounted.mkdir()
    kept = mounted / "kept.sql"

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=mounted)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        editor.focus()
        editor.text = TEXT
        await pilot.press("ctrl+s")
        screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, SaveQueryScreen) else None,
            description="the save browser",
        )
        await _do_not_insert(pilot, screen.file_tree, TEXT, app)
        assert editor.text == TEXT
        name_input = screen.query_one("#save_query_name", Input)
        name_input.value = kept.name
        name_input.focus()
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: kept.is_file() and _location(app) == str(kept.resolve()),
            description="the original query to be saved",
        )
        assert kept.read_bytes() == TEXT.encode("utf-8")
        assert editor.text == TEXT
        assert _label(app) == kept.name

        await run_palette_command(pilot, app, "Open Folder")
        folder_screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, OpenFolderScreen) else None,
            description="the open-folder browser",
        )
        await _do_not_insert(pilot, folder_screen.file_tree, TEXT, app)
        await pilot.press("escape")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, OpenFolderScreen),
            description="open folder to cancel",
        )
        assert editor.text == TEXT
        assert app.data_catalog.file_mounts == [mounted.resolve()]

        await run_palette_command(pilot, app, "Open File")
        file_screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, OpenFileScreen) else None,
            description="the open-file browser",
        )
        await _do_not_insert(pilot, file_screen.file_tree, TEXT, app)
        await pilot.press("escape")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, OpenFileScreen),
            description="open file to cancel",
        )
        assert editor.text == TEXT
        assert _label(app) == kept.name
        assert app.editor_collection.tab_count == 1
        assert kept.read_bytes() == TEXT.encode("utf-8")
        assert app._exception is None


def _chrome(screen: OpenFileScreen | OpenFolderScreen, prefix: str) -> None:
    """The open dialog has the save dialog's name, path, and action rows."""
    assert screen.query_one(f"#{prefix}_name", Input)
    assert screen.query_one(f"#{prefix}_destination")
    assert screen.query_one(f"#{prefix}_error")
    for suffix in (
        "new_file",
        "new_folder",
        "rename",
        "delete",
        "cancel",
        "up",
        "open",
    ):
        assert screen.query(f"#{prefix}_{suffix}")


async def _type_name(pilot: Pilot, field: Input, name: str) -> None:
    """Let a cursor highlight land, then replace the name."""
    await pilot.pause()
    field.value = name


@pytest.mark.asyncio
async def test_open_file_creates_renames_and_deletes(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Open File can create, rename, and delete a file or folder, then read one."""
    docs = tmp_path / "docs"
    nested = docs / "nested"
    nested.mkdir(parents=True)
    notes = docs / "notes.sql"
    write_exact(notes, NOTES)
    secret = nested / "secret.sql"
    write_exact(secret, "secret\n")
    # "a[" and "b].sql" join to a path whose text contains a closing tag.
    bracket_dir = docs / "a["
    bracket_file = bracket_dir / "b].sql"
    dim_name = "x[dim]y.sql"
    draft = docs / "draft.sql"
    renamed = docs / "renamed.sql"
    box = docs / "box"

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=docs)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        await run_palette_command(pilot, app, "Open File")
        screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, OpenFileScreen) else None,
            description="the open-file browser",
        )
        _chrome(screen, "open_file")
        assert screen.query_one("#open_file_outer").border_title == "Open File"
        tree = screen.file_tree
        docs_node = await _cursor_on(pilot, tree, docs.name)
        nested_node = _child(docs_node, "nested")
        assert nested_node is not None and not nested_node.children
        nested_entry = _entry(nested_node)
        assert nested_entry is not None and nested_entry.loaded is False

        name = screen.query_one("#open_file_name", Input)
        await _type_name(pilot, name, bracket_dir.name)
        await pilot.click("#open_file_new_folder")
        await wait_for(
            pilot,
            lambda: bracket_dir.is_dir(),
            description="the bracket folder to be created",
        )
        await _type_name(pilot, name, bracket_file.name)
        await pilot.click("#open_file_new_file")
        await wait_for(
            pilot,
            lambda: bracket_file.is_file() and bracket_file.read_bytes() == b"",
            description="the bracket file to be created empty",
        )
        assert editor.text == ""
        assert isinstance(app.screen, OpenFileScreen)
        docs_node = await _cursor_on(pilot, tree, docs.name)
        nested_node = _child(docs_node, "nested")
        assert nested_node is not None and not nested_node.children
        assert not _has_name(tree.root, secret.name)

        await _cursor_on(pilot, tree, bracket_file.name)
        await pilot.pause()
        await pilot.click("#open_file_delete")
        confirm = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, ConfirmModal) else None,
            description="the delete confirm",
        )
        assert str(bracket_file.resolve()) in confirm.prompt
        await click_confirm_modal(pilot, app, "no")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, OpenFileScreen) and bracket_file.is_file(),
            description="delete to be declined",
        )

        await pilot.click("#open_file_delete")
        await click_confirm_modal(pilot, app, "yes")
        await wait_for(
            pilot,
            lambda: (
                isinstance(app.screen, OpenFileScreen) and not bracket_file.exists()
            ),
            description="the bracket file to be deleted",
        )
        assert editor.text == ""
        assert app._exception is None

        screen = app.screen
        assert isinstance(screen, OpenFileScreen)
        name = screen.query_one("#open_file_name", Input)
        await _cursor_on(pilot, screen.file_tree, docs.name)
        await _type_name(pilot, name, dim_name)
        await pilot.click("#open_file_new_file")
        dim_file = docs / dim_name
        await wait_for(
            pilot,
            lambda: dim_file.is_file(),
            description="the dim file to be created",
        )
        await pilot.click("#open_file_delete")
        confirm = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, ConfirmModal) else None,
            description="the dim delete confirm",
        )
        assert str(dim_file.resolve()) in confirm.prompt
        await click_confirm_modal(pilot, app, "yes")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, OpenFileScreen) and not dim_file.exists(),
            description="the dim file to be deleted",
        )

        screen = app.screen
        assert isinstance(screen, OpenFileScreen)
        name = screen.query_one("#open_file_name", Input)
        await _type_name(pilot, name, draft.name)
        await pilot.click("#open_file_new_file")
        await wait_for(
            pilot,
            lambda: draft.is_file() and draft.read_bytes() == b"",
            description="draft.sql to be created",
        )
        await _type_name(pilot, name, renamed.name)
        await pilot.click("#open_file_rename")
        await wait_for(
            pilot,
            lambda: renamed.is_file() and not draft.exists(),
            description="draft.sql to be renamed",
        )
        assert renamed.read_bytes() == b""

        await _type_name(pilot, name, box.name)
        await pilot.click("#open_file_new_folder")
        await wait_for(
            pilot,
            lambda: box.is_dir(),
            description="box to be created",
        )
        assert isinstance(app.screen, OpenFileScreen)
        await pilot.click("#open_file_delete")
        confirm = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, ConfirmModal) else None,
            description="the folder delete confirm",
        )
        assert "everything inside" in confirm.prompt
        assert str(box.resolve()) in confirm.prompt
        await click_confirm_modal(pilot, app, "yes")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, OpenFileScreen) and not box.exists(),
            description="box to be deleted",
        )

        await _cursor_on(pilot, tree, notes.name)
        await pilot.pause()
        await pilot.click("#open_file_open")
        await wait_for(
            pilot,
            lambda: editor.text == NOTES and _label(app) == notes.name,
            description="notes.sql to be read into one buffer",
        )
        assert _location(app) == str(notes.resolve())
        assert app.editor_collection.tab_count == 1
        assert app._exception is None


@pytest.mark.asyncio
async def test_open_folder_creates_a_folder_then_mounts_it(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Open Folder creates a folder and mounts it. A new file is not a mount."""
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    loose = alpha / "loose.sql"
    gamma = alpha / "gamma"

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=alpha)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        editor.text = TEXT
        await run_palette_command(pilot, app, "Open Folder")
        screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, OpenFolderScreen) else None,
            description="the open-folder browser",
        )
        _chrome(screen, "open_folder")
        assert screen.query_one("#open_folder_outer").border_title == "Open Folder"
        name = screen.query_one("#open_folder_name", Input)
        await _type_name(pilot, name, loose.name)
        await pilot.click("#open_folder_new_file")
        await wait_for(
            pilot,
            lambda: loose.is_file() and loose.read_bytes() == b"",
            description="loose.sql to be created",
        )
        assert isinstance(app.screen, OpenFolderScreen)
        assert app.data_catalog.file_mounts == [alpha.resolve()]
        assert editor.text == TEXT

        await _type_name(pilot, name, gamma.name)
        await pilot.click("#open_folder_new_folder")
        await wait_for(
            pilot,
            lambda: gamma.is_dir(),
            description="gamma to be created",
        )
        await pilot.click("#open_folder_open")
        await wait_for(
            pilot,
            lambda: app.data_catalog.file_mounts == [alpha.resolve(), gamma.resolve()],
            description="gamma to mount beside alpha",
        )
        assert editor.text == TEXT
        assert app.editor_collection.tab_count == 1
        assert app._exception is None


@pytest.mark.asyncio
async def test_save_can_create_a_folder_then_write_the_file(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Save can make a folder, then write the query into a file inside it."""
    mounted = tmp_path / "mounted"
    mounted.mkdir()
    box = mounted / "box"
    notes = box / "notes.sql"

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=mounted)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        editor.focus()
        editor.text = TEXT
        await pilot.press("ctrl+s")
        screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, SaveQueryScreen) else None,
            description="the save browser",
        )
        assert screen.query("#save_query_new_folder")
        assert screen.query("#save_query_type_path")
        name = screen.query_one("#save_query_name", Input)
        await _type_name(pilot, name, box.name)
        await pilot.click("#save_query_new_folder")
        await wait_for(
            pilot,
            lambda: box.is_dir(),
            description="box to be created from save",
        )
        assert isinstance(app.screen, SaveQueryScreen)
        name.value = notes.name
        name.focus()
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: notes.is_file() and _location(app) == str(notes.resolve()),
            description="the query to be saved inside box",
        )
        assert notes.read_bytes() == TEXT.encode("utf-8")
        assert _label(app) == notes.name
        assert app.data_catalog.file_mounts == [mounted.resolve()]
        assert app._exception is None


@pytest.mark.asyncio
async def test_files_tab_creates_reads_renames_and_deletes(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """The Files tab creates, reads, renames, and deletes without closing a query."""
    docs = tmp_path / "docs"
    nested = docs / "nested"
    nested.mkdir(parents=True)
    secret = nested / "secret.sql"
    write_exact(secret, "secret\n")
    created = docs / "created.sql"
    moved = docs / "moved.sql"
    extra = docs / "extra"

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=docs)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        await run_palette_command(pilot, app, "New File")
        prompt = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, NamePrompt) else None,
            description="the new-file prompt",
        )
        field = prompt.query_one("#name_prompt_input", Input)
        field.value = created.name
        field.focus()
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: created.is_file() and created.read_bytes() == b"",
            description="created.sql to appear",
        )
        write_exact(created, NOTES)
        tree = app.data_catalog.file_tree
        assert tree is not None
        nested_node = _child(tree.root, "nested")
        assert nested_node is not None and not nested_node.children
        nested_entry = _entry(nested_node)
        assert nested_entry is not None and nested_entry.loaded is False
        assert not _has_name(tree.root, secret.name)

        await activate(pilot, tree, created.name)
        await wait_for(
            pilot,
            lambda: editor.text == NOTES and _label(app) == created.name,
            description="created.sql to be read",
        )
        assert _location(app) == str(created.resolve())

        await run_palette_command(pilot, app, "Rename File or Folder")
        prompt = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, NamePrompt) else None,
            description="the rename prompt",
        )
        assert str(created.resolve()) in prompt.prompt_text
        field = prompt.query_one("#name_prompt_input", Input)
        field.value = moved.name
        field.focus()
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: (
                moved.is_file()
                and not created.exists()
                and _label(app) == moved.name
                and _location(app) == str(moved.resolve())
            ),
            description="the open query to follow the rename",
        )
        assert moved.read_bytes() == NOTES.encode("utf-8")
        assert editor.text == NOTES

        await run_palette_command(pilot, app, "Delete File or Folder")
        confirm = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, ConfirmModal) else None,
            description="the files delete confirm",
        )
        assert str(moved.resolve()) in confirm.prompt
        await click_confirm_modal(pilot, app, "yes")
        await wait_for(
            pilot,
            lambda: not moved.exists() and _location(app) == "Not a file",
            description="the file to be deleted and the query to keep its text",
        )
        assert editor.text == NOTES
        assert app.editor_collection.tab_count == 1
        assert app.data_catalog.file_mounts == [docs.resolve()]

        await run_palette_command(pilot, app, "New Folder")
        prompt = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, NamePrompt) else None,
            description="the new-folder prompt",
        )
        field = prompt.query_one("#name_prompt_input", Input)
        field.value = extra.name
        field.focus()
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: extra.is_dir(),
            description="extra to be created",
        )
        await run_palette_command(pilot, app, "Delete File or Folder")
        await click_confirm_modal(pilot, app, "yes")
        await wait_for(
            pilot,
            lambda: not extra.exists(),
            description="extra to be deleted",
        )
        assert editor.text == NOTES
        assert app.editor_collection.tab_count == 1
        assert not _has_name(tree.root, secret.name)
        assert app._exception is None
