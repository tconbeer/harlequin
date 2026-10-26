"""The Files tree opens a text file into one query tab, and that tab saves it."""

from __future__ import annotations

from pathlib import Path
from typing import Type

import pytest
from textual.content import Content
from textual.message import Message
from textual.pilot import Pilot
from textual.widgets import Button, Input, Label, Tab
from textual.widgets._toast import Toast
from textual.widgets._tree import TreeNode

from harlequin import Harlequin
from harlequin.app import QuerySubmitted
from harlequin.components.close_buffer_modal import CloseBufferModal
from harlequin.components.code_editor import CodeEditor, QueryTabClose
from harlequin.components.confirm_modal import ConfirmModal
from harlequin.components.data_catalog import FileTree
from harlequin.components.save_query_screen import SaveQueryScreen
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
# A slash cannot be a filename: directory "a[" and file "b].sql".
TAG_DIR = "a["
TAG_SQL_NAME = "b].sql"
TAG_BIN_NAME = "b].bin"
TAG_SQL = "a[/b].sql"
TAG_BIN = "a[/b].bin"


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


async def expand_directory(pilot: Pilot, tree: FileTree, name: str) -> TreeNode:
    """Expand `name` when it is collapsed. Enter toggles an open folder."""
    line = await _line_of(pilot, tree, name)
    node = tree.get_node_at_line(line)
    assert node is not None
    if node.is_expanded:
        return node
    tree.focus()
    tree.move_cursor_to_line(line)
    await pilot.press("enter")
    await wait_for(
        pilot,
        lambda: node.is_expanded,
        description=f"{name} to expand",
    )
    return node


def _toast_plain(app: Harlequin) -> str:
    """The text of every visible toast, rendered the way the toast renders it."""
    lines: list[str] = []
    for toast in app.screen.query(Toast):
        if isinstance(toast, Toast):
            rendered = toast.render()
            lines.append(
                rendered.plain if isinstance(rendered, Content) else str(rendered)
            )
    return "\n".join(lines)


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
            lambda: isinstance(app.screen, CloseBufferModal),
            description="a prompt before closing unsaved edits",
        )
        assert editor.text == UNSAVED
        assert sql_path.read_bytes() == EDITED.encode("utf-8")

        await pilot.click("#close_buffer_cancel")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, CloseBufferModal),
            description="cancel to leave the unsaved file open",
        )
        assert editor.text == UNSAVED
        assert _label(app) == "orders.sql *"

        await pilot.press("ctrl+w")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, CloseBufferModal),
            description="the close prompt again",
        )
        await pilot.click("#close_buffer_discard")
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


@pytest.mark.asyncio
async def test_closing_tag_path_is_a_literal_toast(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Saving and rejecting a path that contains ``a[/b]`` does not crash.

    Notifications are on, so the toast actually renders. The tab keeps the
    file's own name, and the warning keeps the brackets in the path.
    """
    sql_path = tmp_path / TAG_SQL
    bin_path = tmp_path / TAG_BIN
    sql_path.parent.mkdir(parents=True)
    write_exact(sql_path, ORIGINAL)
    bin_path.write_bytes(b"\x00\xffnot text")

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=tmp_path)
    async with app.run_test(size=(120, 36), notifications=True) as pilot:
        editor = await wait_for_editor(pilot, app)
        await pilot.press("f6")
        await pilot.press("k")
        tree = app.data_catalog.file_tree
        assert tree is not None

        await expand_directory(pilot, tree, TAG_DIR)
        await activate(pilot, tree, TAG_SQL_NAME)
        await wait_for(
            pilot,
            lambda: editor.text == ORIGINAL and _label(app) == TAG_SQL_NAME,
            description="the file under a[/b] to open under its own name",
        )

        editor.focus()
        editor.text = EDITED
        await wait_for(
            pilot,
            lambda: _label(app) == f"{TAG_SQL_NAME} *",
            description="the unsaved mark on the bracketed path",
        )
        await pilot.press("ctrl+s")
        await wait_for(
            pilot,
            lambda: (
                sql_path.read_bytes() == EDITED.encode("utf-8")
                and _label(app) == TAG_SQL_NAME
                and TAG_SQL in _toast_plain(app)
                and app._exception is None
            ),
            description="the save toast to show a[/b].sql without crashing",
        )
        assert sql_path.read_bytes() == EDITED.encode("utf-8")
        assert app._exception is None

        await expand_directory(pilot, tree, TAG_DIR)
        await activate(pilot, tree, TAG_BIN_NAME)
        await wait_for(
            pilot,
            lambda: (
                TAG_BIN in _toast_plain(app)
                and "not a text file" in _toast_plain(app)
                and editor.text == EDITED
                and _label(app) == TAG_SQL_NAME
                and app._exception is None
            ),
            description="the non-text warning to show a[/b].bin without crashing",
        )
        assert editor.text == EDITED
        assert app._exception is None


@pytest.mark.asyncio
async def test_open_query_does_not_overwrite_the_previous_file(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Ctrl+O into a path-backed buffer binds the opened file.

    Saving afterwards writes that file. orders.sql keeps the bytes it had.
    """
    other_text = "select 2;\n"
    edited = "select 9;\n"
    orders = tmp_path / "orders.sql"
    other = tmp_path / "other.sql"
    write_exact(orders, ORIGINAL)
    write_exact(other, other_text)

    app = Harlequin(duckdb_adapter((":memory:",)), show_files=tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection
        await pilot.press("f6")
        await pilot.press("k")
        tree = app.data_catalog.file_tree
        assert tree is not None

        await activate(pilot, tree, "orders.sql")
        await wait_for(
            pilot,
            lambda: editor.text == ORIGINAL and _label(app) == "orders.sql",
            description="orders.sql to load into the query editor",
        )
        assert collection.tab_count == 1

        editor.focus()
        await pilot.press("ctrl+o")
        await wait_for(
            pilot,
            lambda: bool(editor.query("#textarea__open_input")),
            description="the open prompt",
        )
        editor.query_one("#textarea__open_input", Input).value = str(other)
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: editor.text == other_text and _label(app) == "other.sql",
            description="other.sql to replace the buffer and its path",
        )
        assert collection.tab_count == 1
        assert collection.active_file_path() == other.resolve()

        editor.focus()
        editor.text = edited
        await wait_for(
            pilot,
            lambda: _label(app) == "other.sql *",
            description="the unsaved mark on other.sql",
        )
        await pilot.press("ctrl+s")
        await wait_for(
            pilot,
            lambda: (
                other.read_bytes() == edited.encode("utf-8")
                and orders.read_bytes() == ORIGINAL.encode("utf-8")
                and _label(app) == "other.sql"
            ),
            description="the save to write other.sql and leave orders.sql alone",
        )
        assert orders.read_bytes() == ORIGINAL.encode("utf-8")
        assert other.read_bytes() == edited.encode("utf-8")


def _prompt_plain(modal: CloseBufferModal) -> str:
    """The close prompt as the label renders it, brackets included."""
    label = modal.query_one("#close_buffer_prompt", Label)
    rendered = label.render()
    plain = getattr(rendered, "plain", None)
    if isinstance(plain, str):
        return plain
    return str(rendered)


async def _close_modal(pilot: Pilot, app: Harlequin) -> CloseBufferModal:
    return await wait_for_value(
        pilot,
        lambda: app.screen if isinstance(app.screen, CloseBufferModal) else None,
        description="the save, close-without-saving, or cancel prompt",
    )


async def _focus_files(pilot: Pilot, app: Harlequin) -> FileTree:
    """Focus the Files tree without leaving that tab when it is already open."""
    catalog = app.data_catalog
    tree = catalog.file_tree
    assert tree is not None
    if catalog.active_pane is not tree.parent:
        await pilot.press("f6")
        await pilot.press("k")
    tree.focus()
    await pilot.pause()
    return tree


async def _empty_query(pilot: Pilot, app: Harlequin) -> None:
    """The only tab is a blank query, and it is not a file."""
    editor = app.editor
    collection = app.editor_collection
    assert editor is not None

    def ready() -> bool:
        tab_id = collection.active
        if collection.tab_count != 1 or tab_id is None or editor.text != "":
            return False
        if collection.location_text() != "Not a file":
            return False
        state = collection.buffer_states.get(tab_id)
        return state is not None and state.path is None and state.text == ""

    await wait_for(pilot, ready, description="one empty query that is not a file")


@pytest.mark.asyncio
async def test_tab_close_control_drops_one_tab(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """The other tab's close control asks, and Don't Save leaves the file tab."""
    sql_path = tmp_path / "orders.sql"
    write_exact(sql_path, ORIGINAL)
    kept = "select 9;\n"
    app = Harlequin(duckdb_adapter((":memory:",)), show_files=tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection
        tree = await _focus_files(pilot, app)
        await activate(pilot, tree, "orders.sql")
        await wait_for(
            pilot,
            lambda: (
                editor.text == ORIGINAL
                and collection.active_file_path() == sql_path.resolve()
            ),
            description="orders.sql to load",
        )
        editor.focus()
        await pilot.press("ctrl+n")
        await wait_for(
            pilot,
            lambda: collection.active == "tab-2" and editor.text == "",
            description="a second query tab",
        )
        editor.text = kept

        def close_control() -> QueryTabClose | None:
            matches = app.query("#tab-2-close")
            if not matches:
                return None
            widget = matches[0]
            if not isinstance(widget, QueryTabClose) or widget.region.width <= 0:
                return None
            return widget

        await wait_for_value(
            pilot,
            close_control,
            description="a visible close control on the second tab",
        )
        await pilot.press("ctrl+k")
        await wait_for(
            pilot,
            lambda: collection.active == "tab-1" and editor.text == ORIGINAL,
            description="the file tab to be active again",
        )
        await pilot.click("#tab-2-close")
        modal = await _close_modal(pilot, app)
        labels = [str(button.label) for button in modal.query(Button)]
        assert labels == ["Cancel", "Don't Save", "Save"]
        await pilot.click("#close_buffer_discard")
        await wait_for(
            pilot,
            lambda: (
                collection.tab_count == 1
                and collection.active == "tab-1"
                and editor.text == ORIGINAL
                and collection.active_file_path() == sql_path.resolve()
                and "tab-2" not in collection.buffer_states
            ),
            description="the file tab to remain after the other tab closes",
        )
        assert sql_path.read_bytes() == ORIGINAL.encode("utf-8")


@pytest.mark.asyncio
async def test_closing_asks_to_save_discard_or_cancel(
    duckdb_adapter: Type[DuckDbAdapter],
    tmp_path: Path,
) -> None:
    """Close from the tab, the palette, and ctrl+w shares one three-choice prompt.

    A clean file closes with no prompt. Cancel keeps a dirty tab. Don't Save
    leaves the file bytes. Save writes them and then closes. A pathless tab
    uses the folder save, and leaving that save keeps the tab.
    """
    sql_path = tmp_path / "orders.sql"
    dim_path = tmp_path / DIM_NAME
    write_exact(sql_path, ORIGINAL)
    write_exact(dim_path, ORIGINAL)
    pathless = "select 3;\n"
    app = Harlequin(duckdb_adapter((":memory:",)), show_files=tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = await wait_for_editor(pilot, app)
        collection = app.editor_collection
        tree = await _focus_files(pilot, app)
        await activate(pilot, tree, "orders.sql")
        await wait_for(
            pilot,
            lambda: editor.text == ORIGINAL and _label(app) == "orders.sql",
            description="orders.sql to load for a clean close",
        )
        assert not collection.has_class("hide-tabs")
        title = app.query_one(".query-tab-title", Label)
        title_plain = getattr(title.render(), "plain", "")
        assert "orders.sql" in title_plain
        mark = app.query_one("#tab-1-close", QueryTabClose)
        mark_plain = getattr(mark.render(), "plain", "")
        assert "×" in mark_plain

        editor.focus()
        await pilot.press("ctrl+w")
        await _empty_query(pilot, app)
        assert not isinstance(app.screen, CloseBufferModal)
        assert not isinstance(app.screen, ConfirmModal)
        assert sql_path.read_bytes() == ORIGINAL.encode("utf-8")

        tree = await _focus_files(pilot, app)
        await activate(pilot, tree, "orders.sql")
        await wait_for(
            pilot,
            lambda: editor.text == ORIGINAL,
            description="orders.sql to load again",
        )
        editor.focus()
        editor.text = UNSAVED
        await wait_for(
            pilot,
            lambda: _label(app) == "orders.sql *",
            description="the unsaved mark before cancel",
        )
        await pilot.click("#tab-1-close")
        modal = await _close_modal(pilot, app)
        assert "orders.sql" in _prompt_plain(modal)
        await pilot.click("#close_buffer_cancel")
        await wait_for(
            pilot,
            lambda: not isinstance(app.screen, CloseBufferModal),
            description="cancel to keep the dirty file tab",
        )
        assert editor.text == UNSAVED
        assert _label(app) == "orders.sql *"
        assert sql_path.read_bytes() == ORIGINAL.encode("utf-8")

        await run_palette_command(pilot, app, "Close Buffer")
        await _close_modal(pilot, app)
        await pilot.click("#close_buffer_discard")
        await _empty_query(pilot, app)
        assert sql_path.read_bytes() == ORIGINAL.encode("utf-8")

        tree = await _focus_files(pilot, app)
        await activate(pilot, tree, "orders.sql")
        await wait_for(
            pilot,
            lambda: editor.text == ORIGINAL,
            description="orders.sql to load before save-and-close",
        )
        editor.focus()
        editor.text = EDITED
        await wait_for(
            pilot,
            lambda: _label(app) == "orders.sql *",
            description="the unsaved mark before save-and-close",
        )
        await pilot.press("ctrl+w")
        await _close_modal(pilot, app)
        await pilot.click("#close_buffer_save")
        await wait_for(
            pilot,
            lambda: sql_path.read_bytes() == EDITED.encode("utf-8"),
            description="save-and-close to write the editor bytes",
        )
        await _empty_query(pilot, app)
        assert sql_path.read_bytes() == EDITED.encode("utf-8")

        tree = await _focus_files(pilot, app)
        await activate(pilot, tree, "orders.sql")
        await wait_for(
            pilot,
            lambda: editor.text == EDITED,
            description="the saved orders.sql to load",
        )
        editor.focus()
        editor.text = "   "
        await wait_for(
            pilot,
            lambda: _label(app) == "orders.sql *",
            description="whitespace to count as unsaved",
        )
        await pilot.press("ctrl+w")
        await _close_modal(pilot, app)
        await pilot.click("#close_buffer_cancel")
        await wait_for(
            pilot,
            lambda: (
                editor.text == "   " and not isinstance(app.screen, CloseBufferModal)
            ),
            description="cancel to keep the whitespace edit",
        )
        assert sql_path.read_bytes() == EDITED.encode("utf-8")
        await pilot.press("ctrl+w")
        await _close_modal(pilot, app)
        await pilot.click("#close_buffer_discard")
        await _empty_query(pilot, app)
        assert sql_path.read_bytes() == EDITED.encode("utf-8")

        tree = await _focus_files(pilot, app)
        await activate(pilot, tree, DIM_NAME)
        await wait_for(
            pilot,
            lambda: editor.text == ORIGINAL and _label(app) == DIM_NAME,
            description="the bracketed file to load",
        )
        editor.focus()
        editor.text = UNSAVED
        await wait_for(
            pilot,
            lambda: _label(app) == f"{DIM_NAME} *",
            description="the unsaved mark on the bracketed file",
        )
        await pilot.press("ctrl+w")
        modal = await _close_modal(pilot, app)
        assert DIM_NAME in _prompt_plain(modal)
        await pilot.click("#close_buffer_cancel")
        await wait_for(
            pilot,
            lambda: (
                editor.text == UNSAVED and not isinstance(app.screen, CloseBufferModal)
            ),
            description="cancel to keep the bracketed file open",
        )
        assert dim_path.read_bytes() == ORIGINAL.encode("utf-8")
        await pilot.press("ctrl+w")
        await _close_modal(pilot, app)
        await pilot.click("#close_buffer_discard")
        await _empty_query(pilot, app)
        assert dim_path.read_bytes() == ORIGINAL.encode("utf-8")

        editor.focus()
        editor.text = pathless
        await pilot.press("ctrl+w")
        modal = await _close_modal(pilot, app)
        assert "Save changes to this query?" in _prompt_plain(modal)
        await pilot.click("#close_buffer_save")
        await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, SaveQueryScreen) else None,
            description="the folder save for a query that is not a file",
        )
        await pilot.press("escape")
        await wait_for(
            pilot,
            lambda: (
                not isinstance(app.screen, SaveQueryScreen) and editor.text == pathless
            ),
            description="leaving the folder save to keep the tab",
        )
        assert not (tmp_path / "from_close.sql").exists()
        assert collection.location_text() == "Not a file"

        await pilot.press("ctrl+w")
        await _close_modal(pilot, app)
        await pilot.click("#close_buffer_save")
        save_screen = await wait_for_value(
            pilot,
            lambda: app.screen if isinstance(app.screen, SaveQueryScreen) else None,
            description="the folder save again",
        )
        await pilot.pause()
        name_input = save_screen.query_one("#save_query_name", Input)
        name_input.value = "from_close.sql"
        name_input.focus()
        await pilot.press("enter")
        target = tmp_path / "from_close.sql"
        await wait_for(
            pilot,
            lambda: (
                target.is_file() and target.read_bytes() == pathless.encode("utf-8")
            ),
            description="the named file to contain the query bytes",
        )
        await _empty_query(pilot, app)
        assert target.read_bytes() == pathless.encode("utf-8")
