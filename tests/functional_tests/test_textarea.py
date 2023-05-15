from typing import List, Union

import pytest
from harlequin.tui import Harlequin
from harlequin.tui.components.key_handlers import Cursor
from harlequin.tui.components.textarea import TextInput


@pytest.fixture
def query() -> List[str]:
    q = [
        "select",
        "    foo,",
        "    bar,",
        "from baz",
    ]
    return [f"{line} " for line in q]


@pytest.mark.parametrize(
    "keys,lines,anchor,cursor,expected_lines,expected_anchor,expected_cursor",
    [
        (
            ["ctrl+a"],
            ["select ", " foo "],
            None,
            Cursor(1, 2),
            None,
            Cursor(0, 0),
            Cursor(1, 4),
        ),
        (
            ["ctrl+shift+right"],
            ["select ", " foo "],
            None,
            Cursor(0, 0),
            None,
            Cursor(0, 0),
            Cursor(0, 6),
        ),
        (
            ["right"],
            ["select ", " foo "],
            Cursor(0, 0),
            Cursor(0, 6),
            None,
            None,
            Cursor(1, 0),
        ),
        (
            ["a"],
            ["select ", " foo "],
            None,
            Cursor(1, 4),
            ["select ", " fooa "],
            None,
            Cursor(1, 5),
        ),
        (
            ["a"],
            ["select ", " foo "],
            Cursor(1, 0),
            Cursor(1, 4),
            ["select ", "a "],
            None,
            Cursor(1, 1),
        ),
        (
            ["enter"],
            ["a ", "a "],
            None,
            Cursor(1, 0),
            ["a ", " ", "a "],
            None,
            Cursor(2, 0),
        ),
        (
            ["enter"],
            ["a ", "a "],
            None,
            Cursor(1, 1),
            ["a ", "a ", " "],
            None,
            Cursor(2, 0),
        ),
        (
            ["enter"],
            ["a() "],
            None,
            Cursor(0, 2),
            ["a( ", "     ", ") "],
            None,
            Cursor(1, 4),
        ),
        (
            ["enter"],
            [" a() "],
            None,
            Cursor(0, 3),
            [" a( ", "     ", " ) "],
            None,
            Cursor(1, 4),
        ),
    ],
)
@pytest.mark.asyncio
async def test_keys(
    app: Harlequin,
    keys: List[str],
    lines: List[str],
    anchor: Union[Cursor, None],
    cursor: Cursor,
    expected_lines: Union[List[str], None],
    expected_anchor: Union[Cursor, None],
    expected_cursor: Cursor,
) -> None:
    if expected_lines is None:
        expected_lines = lines

    async with app.run_test() as pilot:
        widget = app.query_one(TextInput)
        widget.focus()
        widget.lines = lines
        widget.selection_anchor = anchor
        widget.cursor = cursor

        for key in keys:
            await pilot.press(key)

        assert widget.lines == expected_lines
        assert widget.selection_anchor == expected_anchor
        assert widget.cursor == expected_cursor
