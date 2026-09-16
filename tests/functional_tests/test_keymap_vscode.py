from __future__ import annotations

from textwrap import dedent
from typing import Awaitable, Callable
from unittest.mock import MagicMock

import pytest
from textual.css.query import NoMatches
from textual.widgets import Input

from harlequin import Harlequin
from tests.functional_tests.helpers import wait_for_any_table, wait_for_editor

QUERY = dedent(
    """
    select *
    from
        (
            values
                (1, 2, 3),
                (4, 5, 6),
                (7, 8, 9),
                (10, 11, 12),
                (13, 14, 15),
                (16, 17, 18),
                (19, 20, 21)
        ) foo(a, b, c)
"""
).strip()


@pytest.mark.asyncio
async def test_editor_bindings(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_pyperclip: MagicMock,
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)

        q = QUERY
        editor.text = q
        assert editor.selection.start == editor.selection.end == (0, 0)

        # simple navigation
        await pilot.press("down")
        assert editor.selection.start == editor.selection.end == (1, 0)
        await pilot.press("right")
        assert editor.selection.start == editor.selection.end == (1, 1)
        await pilot.press("right")
        assert editor.selection.start == editor.selection.end == (1, 2)
        await pilot.press("left")
        assert editor.selection.start == editor.selection.end == (1, 1)
        await pilot.press("up")
        assert editor.selection.start == editor.selection.end == (0, 1)
        await pilot.press("ctrl+right")
        assert editor.selection.start == editor.selection.end == (0, 6)
        await pilot.press("ctrl+left")
        assert editor.selection.start == editor.selection.end == (0, 0)
        await pilot.press("ctrl+end")
        assert editor.selection.start == editor.selection.end == (11, 18)
        await pilot.press("ctrl+home")
        assert editor.selection.start == editor.selection.end == (0, 0)

        # simple selection
        await pilot.press("shift+down")
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (1, 0)
        await pilot.press("shift+right")
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (1, 1)
        await pilot.press("shift+right")
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (1, 2)
        await pilot.press("shift+left")
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (1, 1)
        await pilot.press("shift+up")
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (0, 1)
        await pilot.press("ctrl+shift+right")
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (0, 6)
        await pilot.press("ctrl+shift+left")
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (0, 0)
        await pilot.press("ctrl+shift+end")
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (11, 18)
        await pilot.press("ctrl+shift+home")
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (0, 0)
        await pilot.press("ctrl+a")
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (11, 18)

        # cut/copy/paste
        await pilot.press("ctrl+c")
        assert editor.text == QUERY
        assert editor.text_input is not None
        assert editor.text_input.clipboard == QUERY
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (11, 18)
        await pilot.press("ctrl+x")
        assert editor.text == ""
        assert editor.text_input.clipboard == QUERY
        assert editor.selection.start == (0, 0)
        assert editor.selection.end == (0, 0)
        await pilot.press("ctrl+v")
        assert editor.text == QUERY
        assert editor.selection.start == editor.selection.end == (11, 18)

        await pilot.press("a")
        assert editor.text == QUERY + "a"
        await pilot.press("escape")  # dismiss autocomplete
        await pilot.press("enter")
        assert editor.text == QUERY + "a\n    "

        # undo/redo
        await pilot.press("ctrl+z")
        assert editor.text == QUERY + "a"
        await pilot.press("ctrl+y")
        assert editor.text == QUERY + "a\n    "

        # delete
        await pilot.press("backspace")
        assert editor.text == QUERY + "a\n   "
        await pilot.press("shift+delete")
        assert editor.text == QUERY + "a"
        await pilot.press("backspace")
        assert editor.text == QUERY
        await pilot.press("ctrl+home")
        await pilot.press("delete")
        assert editor.text == QUERY[1:]

        # find
        await pilot.press("ctrl+f")
        assert app.query_one("#textarea__find_input")
        await pilot.press("escape")
        with pytest.raises(NoMatches):
            _ = app.query_one("#textarea__find_input")
        await pilot.press("f3")
        assert app.query_one("#textarea__find_input")
        await pilot.press("escape")
        with pytest.raises(NoMatches):
            _ = app.query_one("#textarea__find_input")

        # goto line
        await pilot.press("ctrl+g")
        assert app.query_one("#textarea__gotoline_input")
        await pilot.press("escape")
        with pytest.raises(NoMatches):
            _ = app.query_one("#textarea__gotoline_input")

        # save
        await pilot.press("ctrl+s")
        assert app.query_one("#textarea__save_input")
        await pilot.press("escape")
        with pytest.raises(NoMatches):
            _ = app.query_one("#textarea__save_input")

        # open
        await pilot.press("ctrl+o")
        assert app.query_one("#textarea__open_input")
        await pilot.press("escape")
        with pytest.raises(NoMatches):
            _ = app.query_one("#textarea__open_input")


@pytest.mark.asyncio
async def test_results_viewer_bindings(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_pyperclip: MagicMock,
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        editor = await wait_for_editor(pilot, app)

        q = QUERY
        editor.text = q
        await pilot.press("ctrl+j")

        table = await wait_for_any_table(pilot, app)

        assert table is not None
        assert table.cursor_coordinate == (0, 0)
        assert table.selection_anchor_coordinate is None

        # simple navigation
        await pilot.press("down")
        assert table.cursor_coordinate == (1, 0)
        assert table.selection_anchor_coordinate is None
        await pilot.press("right")
        assert table.cursor_coordinate == (1, 1)
        assert table.selection_anchor_coordinate is None
        await pilot.press("right")
        assert table.cursor_coordinate == (1, 2)
        assert table.selection_anchor_coordinate is None
        await pilot.press("left")
        assert table.cursor_coordinate == (1, 1)
        assert table.selection_anchor_coordinate is None
        await pilot.press("up")
        assert table.cursor_coordinate == (0, 1)
        assert table.selection_anchor_coordinate is None
        await pilot.press("ctrl+right")
        assert table.cursor_coordinate == (0, 2)
        assert table.selection_anchor_coordinate is None
        await pilot.press("ctrl+left")
        assert table.cursor_coordinate == (0, 0)
        assert table.selection_anchor_coordinate is None
        await pilot.press("ctrl+end")
        assert table.cursor_coordinate == (6, 2)
        assert table.selection_anchor_coordinate is None
        await pilot.press("ctrl+home")
        assert table.cursor_coordinate == (0, 0)
        assert table.selection_anchor_coordinate is None

        # simple selection
        await pilot.press("shift+down")
        assert table.cursor_coordinate == (1, 0)
        assert table.selection_anchor_coordinate == (0, 0)
        await pilot.press("shift+right")
        assert table.cursor_coordinate == (1, 1)
        assert table.selection_anchor_coordinate == (0, 0)
        await pilot.press("shift+right")
        assert table.cursor_coordinate == (1, 2)
        assert table.selection_anchor_coordinate == (0, 0)
        await pilot.press("shift+left")
        assert table.cursor_coordinate == (1, 1)
        assert table.selection_anchor_coordinate == (0, 0)
        await pilot.press("shift+up")
        assert table.cursor_coordinate == (0, 1)
        assert table.selection_anchor_coordinate == (0, 0)
        await pilot.press("ctrl+shift+right")
        assert table.cursor_coordinate == (0, 2)
        assert table.selection_anchor_coordinate == (0, 0)
        await pilot.press("ctrl+shift+left")
        assert table.cursor_coordinate == (0, 0)
        assert table.selection_anchor_coordinate == (0, 0)
        await pilot.press("ctrl+shift+end")
        assert table.cursor_coordinate == (6, 2)
        assert table.selection_anchor_coordinate == (0, 0)
        await pilot.press("ctrl+shift+home")
        assert table.cursor_coordinate == (0, 0)
        assert table.selection_anchor_coordinate == (0, 0)
        await pilot.press("ctrl+a")
        assert table.cursor_coordinate == (6, 2)
        assert table.selection_anchor_coordinate == (0, 0)

        # copy
        await pilot.press("ctrl+c")
        assert editor.text_input is not None
        assert editor.text_input.clipboard.startswith("1\t2\t3")


@pytest.mark.asyncio
async def test_editor_bindings_do_not_beat_the_find_input(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """ctrl+w and ctrl+k edit the find input, rather than closing or switching a buffer.

    Both are bound to a `code_editor.*` action on `EditorCollection`, an ancestor
    of the find input, and the input's own editing keys have to win.
    """
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        await wait_for_editor(pilot, app)

        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        assert app.editor_collection.tab_count == 2
        assert app.editor_collection.active == "tab-2"

        await pilot.press("ctrl+f")
        find_input = app.query_one("#textarea__find_input", Input)
        await pilot.press("f", "o", "o", "space", "b", "a", "r")
        assert find_input.value == "foo bar"

        await pilot.press("ctrl+w")
        assert find_input.value == "foo "
        assert app.editor_collection.tab_count == 2

        await pilot.press("ctrl+k")
        assert find_input.value == "foo "
        assert app.editor_collection.active == "tab-2"
