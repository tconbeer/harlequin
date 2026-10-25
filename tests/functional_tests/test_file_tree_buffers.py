"""The Files tree opens a text file into one query tab, and that tab saves it."""

from __future__ import annotations

from pathlib import Path
from typing import Type

import pytest
from textual.message import Message
from textual.pilot import Pilot
from textual.widgets import Tab
from textual.widgets._tree import TreeNode

from harlequin import Harlequin
from harlequin.app import QuerySubmitted
from harlequin.components.code_editor import CodeEditor
from harlequin.components.confirm_modal import ConfirmModal
from harlequin.components.data_catalog import FileTree
from harlequin_duckdb.adapter import DuckDbAdapter
from tests.functional_tests.helpers import wait_for_editor
from tests.functional_tests.test_command_palette import run_palette_command
from tests.waiting import wait_for, wait_for_messages, wait_for_value

ORIGINAL = "select 1;\n"
EDITED = "select 42;\n"
UNSAVED = "select 99;\n"
BLANK_QUERY = "select 7 as opened_file"
DIM_NAME = "x[dim]y.sql"
FINAL_NAME = "report[final].sql"


def write_exact(path: Path, text: str) -> None:
    """Write `text` as its utf-8 bytes, with no newline translation."""
    path.write_bytes(text.encode("utf-8"))


async def _line_of(pilot: Pilot, tree: FileTree, name: str) -> int:
    """The tree line whose node is `name`, once the directory listing has it."""

    def listed() -> int | None:
        last = tree.last_line
        for line in range(last + 1):
            node = tree.get_node_at_line(line)
            if node is None or node.data is None:
                continue
            if Path(node.data.path).name == name:
                return line
        return None

    return await wait_for_value(
        pilot, listed, description=f"{name} to be listed in the file tree"
    )


async def activate(pilot: Pilot, tree: FileTree, name: str) -> TreeNode:
    """Move the real file-tree cursor onto `name` and press enter."""
    line = await _line_of(pilot, tree, name)
    node = tree.get_node_at_line(line)
    assert node is not None
    tree.focus()
    tree.move_cursor_to_line(line)
    await pilot.press("enter")
    return node


def _label(app: Harlequin) -> str:
    collection = app.editor_collection
    tab_id = collection.active
    if tab_id is None:
        return ""
    tab = collection.tabs.get_tab(tab_id)
    if tab is None:
        return ""
    return tab.label_text


@pytest.mark.asyncio
async def test_file_tree_opens_edits_and_saves_one_buffer(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Enter on a text file loads one named tab; save writes it; discard confirms.

    A directory only expands, and a non-text file leaves the editor alone.
    A new blank buffer can still submit its SQL.
    """
    sql_path = tmp_path / "orders.sql"
    write_exact(sql_path, ORIGINAL)
    nested = tmp_path / "nested"
    nested.mkdir()
    write_exact(nested / "inside.sql", "select 2;\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\xffnot text")

    messages: list[Message] = []
    app = Harlequin(duckdb_adapter((":memory:",)), show_files=tmp_path)
    async with app.run_test(size=(120, 36), message_hook=messages.append) as pilot:
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection
        assert collection.tab_count == 1

        await pilot.press("f6")  # focus the data catalog
        await pilot.press("k")  # Files tab
        tree = app.data_catalog.file_tree
        assert tree is not None

        await activate(pilot, tree, "orders.sql")
        await wait_for(
            pilot,
            lambda: editor.text == ORIGINAL and "orders.sql" in _label(app),
            description="orders.sql to load into the query editor",
        )
        assert collection.tab_count == 1
        assert not collection.has_class("hide-tabs")
        assert "*" not in _label(app)
        opened = collection.tab_count

        await activate(pilot, tree, "orders.sql")
        await pilot.pause()
        assert editor.text == ORIGINAL
        assert collection.tab_count == opened

        directory = await activate(pilot, tree, "nested")
        await wait_for(
            pilot,
            lambda: directory.is_expanded,
            description="the subdirectory to expand",
        )
        assert editor.text == ORIGINAL
        assert collection.tab_count == opened

        await activate(pilot, tree, "blob.bin")
        await wait_for(
            pilot,
            lambda: any(
                "not a text file" in str(note.message) for note in app._notifications
            ),
            description="a warning that blob.bin is not text",
        )
        assert editor.text == ORIGINAL
        assert collection.tab_count == opened

        editor.focus()
        editor.text = EDITED
        await wait_for(
            pilot,
            lambda: _label(app) == "orders.sql *",
            description="the unsaved mark on orders.sql",
        )

        await pilot.press("ctrl+s")
        await wait_for(
            pilot,
            lambda: (
                sql_path.read_bytes() == EDITED.encode("utf-8")
                and _label(app) == "orders.sql"
            ),
            description="orders.sql to be saved and the unsaved mark to clear",
        )

        editor.text = "   "
        await wait_for(
            pilot,
            lambda: _label(app) == "orders.sql *",
            description="the unsaved mark after the file was wiped to whitespace",
        )
        await run_palette_command(pilot, app, "Close All Buffers")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, ConfirmModal),
            description="a confirm before discarding a blanked file",
        )
        assert editor.text == "   "
        assert sql_path.read_bytes() == EDITED.encode("utf-8")
        await pilot.click("#no")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, ConfirmModal),
            description="the close-all prompt to close",
        )
        assert editor.text == "   "

        editor.text = UNSAVED
        await wait_for(
            pilot,
            lambda: _label(app) == "orders.sql *",
            description="the unsaved mark after another edit",
        )
        await pilot.press("ctrl+w")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, ConfirmModal),
            description="a confirm before discarding unsaved edits",
        )
        assert editor.text == UNSAVED
        assert sql_path.read_bytes() == EDITED.encode("utf-8")

        await pilot.click("#no")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, ConfirmModal),
            description="the discard prompt to close",
        )
        assert editor.text == UNSAVED
        assert _label(app) == "orders.sql *"

        await pilot.press("ctrl+w")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, ConfirmModal),
            description="the discard prompt again",
        )
        await pilot.click("#yes")
        await wait_for(
            pilot,
            lambda: (
                editor.text == ""
                and not any(
                    state.path is not None and state.path.name == "orders.sql"
                    for state in collection.buffer_states.values()
                )
            ),
            description="the discarded file buffer to be emptied",
        )
        assert sql_path.read_bytes() == EDITED.encode("utf-8")

        def blank_buffer_ready() -> bool:
            tab_id = collection.active
            if collection.tab_count != 2 or tab_id is None:
                return False
            state = collection.buffer_states.get(tab_id)
            return state is not None and state.path is None and editor.text == ""

        await pilot.press("ctrl+n")
        await wait_for(
            pilot,
            blank_buffer_ready,
            description="a new blank query buffer",
        )
        editor.focus()
        editor.text = BLANK_QUERY
        await pilot.press("ctrl+j")
        # The hook records a bubbling message once per widget it visits.
        submitted = await wait_for_messages(pilot, messages, CodeEditor.Submitted)
        assert {message.text for message in submitted} == {BLANK_QUERY}
        queries = await wait_for_messages(pilot, messages, QuerySubmitted)
        assert {tuple(message.queries) for message in queries} == {(BLANK_QUERY,)}


@pytest.mark.asyncio
async def test_bracketed_filename_is_a_literal_label(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """A file name with brackets is the tab text, and save writes the editor bytes."""
    crlf_original = "select 2;\r\n"
    crlf_edited = "select 9;\r\n"
    dim_path = tmp_path / DIM_NAME
    final_path = tmp_path / FINAL_NAME
    write_exact(dim_path, ORIGINAL)
    write_exact(final_path, crlf_original)

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection
        await pilot.press("f6")
        await pilot.press("k")
        tree = app.data_catalog.file_tree
        assert tree is not None

        await activate(pilot, tree, DIM_NAME)
        await wait_for(
            pilot,
            lambda: editor.text == ORIGINAL and _label(app) == DIM_NAME,
            description="the bracketed file name to be the tab label",
        )
        assert collection.tab_count == 1

        await activate(pilot, tree, FINAL_NAME)
        await wait_for(
            pilot,
            lambda: editor.text == crlf_original and _label(app) == FINAL_NAME,
            description="the second bracketed name to title its own tab",
        )
        assert collection.tab_count == 2
        assert DIM_NAME in {
            tab.label_text for tab in collection.tabs.query(Tab) if isinstance(tab, Tab)
        }

        editor.focus()
        editor.text = "select 9;\n"
        await wait_for(
            pilot,
            lambda: editor.text == crlf_edited and _label(app) == f"{FINAL_NAME} *",
            description="the edit to keep CRLF and mark the bracketed tab",
        )
        await pilot.press("ctrl+s")
        await wait_for(
            pilot,
            lambda: (
                final_path.read_bytes() == editor.text.encode("utf-8")
                and _label(app) == FINAL_NAME
            ),
            description="the saved bytes to match the editor, including CRLF",
        )
        assert final_path.read_bytes() == crlf_edited.encode("utf-8")
        assert b"\r\r\n" not in final_path.read_bytes()
