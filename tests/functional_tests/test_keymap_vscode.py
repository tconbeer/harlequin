from __future__ import annotations

from textwrap import dedent

import pytest
from harlequin import Harlequin
from textual.css.query import NoMatches

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
async def test_editor_bindings(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None:
            await pilot.pause()

        q = QUERY
        app.editor.text = q
        assert app.editor.selection.start == app.editor.selection.end == (0, 0)

        # simple navigation
        await pilot.press("down")
        assert app.editor.selection.start == app.editor.selection.end == (1, 0)
        await pilot.press("right")
        assert app.editor.selection.start == app.editor.selection.end == (1, 1)
        await pilot.press("right")
        assert app.editor.selection.start == app.editor.selection.end == (1, 2)
        await pilot.press("left")
        assert app.editor.selection.start == app.editor.selection.end == (1, 1)
        await pilot.press("up")
        assert app.editor.selection.start == app.editor.selection.end == (0, 1)
        await pilot.press("ctrl+right")
        assert app.editor.selection.start == app.editor.selection.end == (0, 6)
        await pilot.press("ctrl+left")
        assert app.editor.selection.start == app.editor.selection.end == (0, 0)
        await pilot.press("ctrl+end")
        assert app.editor.selection.start == app.editor.selection.end == (11, 18)
        await pilot.press("ctrl+home")
        assert app.editor.selection.start == app.editor.selection.end == (0, 0)

        # simple selection
        await pilot.press("shift+down")
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (1, 0)
        await pilot.press("shift+right")
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (1, 1)
        await pilot.press("shift+right")
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (1, 2)
        await pilot.press("shift+left")
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (1, 1)
        await pilot.press("shift+up")
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (0, 1)
        await pilot.press("ctrl+shift+right")
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (0, 6)
        await pilot.press("ctrl+shift+left")
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (0, 0)
        await pilot.press("ctrl+shift+end")
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (11, 18)
        await pilot.press("ctrl+shift+home")
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (0, 0)
        await pilot.press("ctrl+a")
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (11, 18)

        # cut/copy/paste
        await pilot.press("ctrl+c")
        assert app.editor.text == QUERY
        assert app.editor.text_input.clipboard == QUERY
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (11, 18)
        await pilot.press("ctrl+x")
        assert app.editor.text == ""
        assert app.editor.text_input.clipboard == QUERY
        assert app.editor.selection.start == (0, 0)
        assert app.editor.selection.end == (0, 0)
        await pilot.press("ctrl+v")
        assert app.editor.text == QUERY
        assert app.editor.selection.start == app.editor.selection.end == (11, 18)

        await pilot.press("a")
        assert app.editor.text == QUERY + "a"
        await pilot.press("escape")  # dismiss autocomplete
        await pilot.press("enter")
        assert app.editor.text == QUERY + "a\n    "

        # undo/redo
        await pilot.press("ctrl+z")
        assert app.editor.text == QUERY + "a"
        await pilot.press("ctrl+y")
        assert app.editor.text == QUERY + "a\n    "

        # delete
        await pilot.press("backspace")
        assert app.editor.text == QUERY + "a\n   "
        await pilot.press("shift+delete")
        assert app.editor.text == QUERY + "a"
        await pilot.press("backspace")
        assert app.editor.text == QUERY
        await pilot.press("ctrl+home")
        await pilot.press("delete")
        assert app.editor.text == QUERY[1:]

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
async def test_results_viewer_bindings(app: Harlequin) -> None:
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None:
            await pilot.pause()

        q = QUERY
        app.editor.text = q
        await pilot.press("ctrl+j")

        while (table := app.results_viewer.get_visible_table()) is None:
            await pilot.pause()

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
        assert app.editor.text_input.clipboard.startswith("1\t2\t3")
