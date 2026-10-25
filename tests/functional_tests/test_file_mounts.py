"""Several Files roots, save-by-folder, and a buffer cache that can store a path."""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Type

import pytest
from textual.pilot import Pilot
from textual.widgets import Input
from textual.widgets.text_area import Selection

from harlequin import Harlequin
from harlequin.components.confirm_modal import ConfirmModal
from harlequin.components.data_catalog import FileTree
from harlequin.components.save_query_screen import SaveQueryScreen
from harlequin.editor_cache import BufferState, Cache, get_cache_file, load_cache
from harlequin_duckdb.adapter import DuckDbAdapter
from tests.functional_tests.helpers import wait_for_editor
from tests.functional_tests.test_command_palette import (
    click_confirm_modal,
    run_palette_command,
    submit_location,
)
from tests.functional_tests.test_file_tree_buffers import (
    _label,
    activate,
    expand_directory,
    write_exact,
)
from tests.waiting import wait_for, wait_for_value

ORIGINAL = "select 1;\n"
EDITED = "select 42;\n"


def _location(app: Harlequin) -> str:
    return app.editor_collection.location_text()


async def _attach(pilot: Pilot, app: Harlequin, path: Path) -> None:
    await run_palette_command(pilot, app, "Show Files")
    await submit_location(pilot, app, str(path))
    await wait_for(
        pilot,
        lambda: path.resolve() in app.data_catalog.file_mounts,
        description=f"{path.name} to be mounted",
    )


async def _save_as(pilot: Pilot, app: Harlequin, folder: Path, name: str) -> None:
    """Ctrl+S, point the tree at `folder`, and set only the file name."""
    editor = app.editor
    assert editor is not None
    editor.focus()
    await pilot.press("ctrl+s")
    screen = await wait_for_value(
        pilot,
        lambda: app.screen if isinstance(app.screen, SaveQueryScreen) else None,
        description="the folder-and-name save dialog",
    )
    tree = screen.file_tree
    line = await wait_for_value(
        pilot,
        lambda: _line_named(tree, folder.name),
        description=f"{folder.name} in the save tree",
    )
    tree.move_cursor_to_line(line)
    name_input = screen.query_one("#save_query_name", Input)
    name_input.value = name
    name_input.focus()
    await pilot.press("enter")


def _line_named(tree: FileTree, name: str) -> int | None:
    for line in range(tree.last_line + 1):
        node = tree.get_node_at_line(line)
        if node is None or node.data is None:
            continue
        if Path(node.data.path).name == name:
            return line
    return None


@pytest.mark.asyncio
async def test_mount_attach_and_close_keeps_buffers(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Two roots, a third attached, one closed. The open buffer stays."""
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    gamma = tmp_path / "gamma"
    nested = alpha / "nested"
    for directory in (alpha, beta, gamma, nested):
        directory.mkdir()
    orders = alpha / "orders.sql"
    write_exact(orders, ORIGINAL)
    (alpha / "blob.bin").write_bytes(b"\x00\x01")
    (beta / "b.sql").write_bytes(b"select 2;\n")
    (gamma / "c.sql").write_bytes(b"select 3;\n")

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=alpha)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        await _attach(pilot, app, beta)
        await _attach(pilot, app, gamma)
        assert app.data_catalog.file_mounts == [
            alpha.resolve(),
            beta.resolve(),
            gamma.resolve(),
        ]

        tree = app.data_catalog.file_tree
        assert tree is not None
        await activate(pilot, tree, "orders.sql")
        await wait_for(
            pilot,
            lambda: editor.text == ORIGINAL and _label(app) == "orders.sql",
            description="orders.sql to open into one buffer",
        )
        assert app.editor_collection.tab_count == 1
        await activate(pilot, tree, "orders.sql")
        await pilot.pause()
        assert app.editor_collection.tab_count == 1
        assert _label(app) == "orders.sql"

        folder = await expand_directory(pilot, tree, "nested")
        assert folder.is_expanded
        assert editor.text == ORIGINAL
        tree.focus()
        tree.move_cursor_to_line(folder.line)
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: not folder.is_expanded,
            description="nested to collapse",
        )
        assert editor.text == ORIGINAL
        assert app.editor_collection.tab_count == 1

        await activate(pilot, tree, "blob.bin")
        await pilot.pause()
        assert editor.text == ORIGINAL
        assert _label(app) == "orders.sql"
        assert app.editor_collection.tab_count == 1

        line = await wait_for_value(
            pilot,
            lambda: _line_named(tree, "beta"),
            description="beta to be listed",
        )
        tree.move_cursor_to_line(line)
        await run_palette_command(pilot, app, "Close Folder")
        await wait_for(
            pilot,
            lambda: app.data_catalog.file_mounts == [alpha.resolve(), gamma.resolve()],
            description="beta to close and the other mounts to stay",
        )
        assert editor.text == ORIGINAL
        assert _label(app) == "orders.sql"
        assert app.editor_collection.tab_count == 1
        assert orders.read_bytes() == ORIGINAL.encode()


@pytest.mark.asyncio
async def test_save_unbound_by_folder_writes_exact_bytes(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """A Tab N buffer is saved by folder plus name, then written again as bytes."""
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    crlf = beta / "crlf.sql"
    crlf.write_bytes(b"select 2;\r\n")

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=alpha)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        await _attach(pilot, app, beta)
        assert _label(app) == "" or _label(app).startswith("Tab ")
        assert _location(app) == "Not a file"

        editor.focus()
        editor.text = ORIGINAL
        await _save_as(pilot, app, beta, "notes.sql")
        notes = beta / "notes.sql"
        await wait_for(
            pilot,
            lambda: (
                _label(app) == "notes.sql"
                and _location(app) == str(notes.resolve())
                and notes.is_file()
            ),
            description="the tab to bind notes.sql",
        )
        assert _location(app) != "Not a file"
        assert notes.read_bytes() == editor.text.encode()
        assert app.editor_collection.tab_count == 1

        editor.text = EDITED
        await wait_for(
            pilot,
            lambda: _label(app) == "notes.sql *",
            description="the unsaved mark",
        )
        editor.focus()
        await pilot.press("ctrl+w")
        await click_confirm_modal(pilot, app, "no")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, ConfirmModal),
            description="cancel to leave the dirty file open",
        )
        assert editor.text == EDITED
        assert _label(app) == "notes.sql *"

        editor.focus()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, SaveQueryScreen)
        await wait_for(
            pilot,
            lambda: (
                notes.read_bytes() == editor.text.encode()
                and _label(app) == "notes.sql"
            ),
            description="the next save to write notes.sql with no path prompt",
        )

        tree = app.data_catalog.file_tree
        assert tree is not None
        await activate(pilot, tree, "crlf.sql")
        await wait_for(
            pilot,
            lambda: editor.text == "select 2;\r\n" and _label(app) == "crlf.sql",
            description="the CRLF fixture to open",
        )
        editor.text = "select 9;\n"
        await wait_for(
            pilot,
            lambda: editor.text == "select 9;\r\n",
            description="the editor to keep the file's newline",
        )
        editor.focus()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, SaveQueryScreen)
        saved = crlf.read_bytes()
        assert saved == editor.text.encode()
        assert b"\r\r\n" not in saved


@pytest.mark.asyncio
async def test_save_refuses_a_second_buffer_and_asks_before_overwrite(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """One buffer per path, and an existing file is not replaced without a yes."""
    folder = tmp_path / "sql"
    folder.mkdir()
    existing = folder / "existing.sql"
    write_exact(existing, "keep\n")
    other = folder / "other.sql"
    write_exact(other, "disk\n")

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=folder)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        tree = app.data_catalog.file_tree
        assert tree is not None
        await activate(pilot, tree, "existing.sql")
        await wait_for(
            pilot,
            lambda: _label(app) == "existing.sql",
            description="existing.sql to open",
        )

        editor.focus()
        await pilot.press("ctrl+n")
        await wait_for(
            pilot,
            lambda: _location(app) == "Not a file",
            description="a new unbound tab",
        )
        editor.text = "elsewhere\n"
        await _save_as(pilot, app, folder, "existing.sql")
        await pilot.pause()
        paths = [
            state.path
            for state in app.editor_collection.buffer_states.values()
            if state.path is not None
        ]
        assert paths == [existing.resolve()]
        assert app.editor_collection.tab_count == 2
        assert existing.read_bytes() == b"keep\n"
        unbound = [
            state
            for state in app.editor_collection.buffer_states.values()
            if state.path is None
        ]
        assert len(unbound) == 1

        editor.focus()
        await pilot.press("ctrl+k")
        await wait_for(
            pilot,
            lambda: _location(app) == "Not a file",
            description="the unbound tab",
        )
        await _save_as(pilot, app, folder, "other.sql")
        await click_confirm_modal(pilot, app, "no")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, ConfirmModal),
            description="overwrite to be declined",
        )
        assert other.read_bytes() == b"disk\n"
        assert _location(app) == "Not a file"
        assert (
            sum(
                state.path == other.resolve()
                for state in app.editor_collection.buffer_states.values()
            )
            == 0
        )


@pytest.mark.use_cache
@pytest.mark.asyncio
async def test_cache_quit_restores_bound_and_unbound(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quit's cache writer and the next launch's loader keep a bound path."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr("harlequin.editor_cache.user_cache_dir", lambda **_: cache_dir)
    folder = tmp_path / "sql"
    folder.mkdir()
    orders = folder / "orders.sql"
    write_exact(orders, ORIGINAL)

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=folder)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        tree = app.data_catalog.file_tree
        assert tree is not None
        await activate(pilot, tree, "orders.sql")
        await wait_for(
            pilot,
            lambda: _label(app) == "orders.sql",
            description="orders.sql to open before quit",
        )
        editor.focus()
        await pilot.press("ctrl+n")
        await wait_for(
            pilot,
            lambda: _location(app) == "Not a file",
            description="an unbound tab before quit",
        )
        editor.text = "select 9;\n"
        await pilot.press("ctrl+q")

    written = load_cache()
    assert written is not None
    assert [buffer.path for buffer in written.buffers] == [str(orders.resolve()), None]
    orders.write_bytes(b"stale\n")

    restored = Harlequin(duckdb_adapter((":memory:",)))
    async with restored.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, restored)
        await wait_for(
            pilot,
            lambda: _label(restored) == "Tab 2" and _location(restored) == "Not a file",
            description="the unbound tab to come back as not a file",
        )
        assert editor.text == "select 9;\n"
        editor.focus()
        await pilot.press("ctrl+k")
        await wait_for(
            pilot,
            lambda: (
                _label(restored) == "orders.sql"
                and _location(restored) == str(orders.resolve())
            ),
            description="the bound tab to come back on orders.sql",
        )
        editor.focus()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(restored.screen, SaveQueryScreen)
        assert orders.read_bytes() == editor.text.encode()


@pytest.mark.use_cache
@pytest.mark.asyncio
async def test_cache_version_1_loads_without_a_path(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version-1 cache is selection and text only. Launch invents no path."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr("harlequin.editor_cache.user_cache_dir", lambda **_: cache_dir)
    legacy = BufferState(
        selection=Selection((0, 1), (0, 4)),
        text="select 1;\n",
    )
    del legacy.__dict__["path"]
    cache_dir.mkdir()
    (cache_dir / "cache-1.pickle").write_bytes(
        pickle.dumps(Cache(focus_index=0, buffers=[legacy]))
    )
    assert not get_cache_file().exists()

    app = Harlequin(duckdb_adapter((":memory:",)))
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        await wait_for(
            pilot,
            lambda: editor.text == "select 1;\n" and _location(app) == "Not a file",
            description="version-1 text to load with no path",
        )
        assert app.editor_collection.active_file_path() is None
        assert all(
            state.path is None for state in app.editor_collection.buffer_states.values()
        )
        assert editor.selection == Selection((0, 1), (0, 4))
