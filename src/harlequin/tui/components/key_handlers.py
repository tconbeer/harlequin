import re
from typing import List, NamedTuple

from textual import log

WWB = re.compile(r"\W*\w+\b")


class Cursor(NamedTuple):
    lno: int
    pos: int


def handle_arrow(key: str, lines: List[str], cursor: Cursor) -> Cursor:
    arrow = key.split("+")[-1]
    if "ctrl" in key:
        assert arrow not in ("up", "down"), "ctrl+up/down should be handled first"
        if arrow == "right":
            return _handle_ctrl_right(lines, cursor)
        else:  # if arrow == "left":
            return _handle_ctrl_left(lines, cursor)
    else:
        if arrow == "right":
            return _handle_right(lines, cursor)
        elif arrow == "left":
            return _handle_left(lines, cursor)
        elif arrow == "down":
            return _handle_down(lines, cursor)
        else:  # arrow == "up":
            return _handle_up(lines, cursor)


def _handle_right(lines: List[str], cursor: Cursor) -> Cursor:
    max_x = len(lines[cursor.lno]) - 1
    max_y = len(lines) - 1
    if cursor.lno == max_y:
        return Cursor(lno=max_y, pos=min(max_x, cursor.pos + 1))
    elif cursor.pos == max_x:
        return Cursor(lno=cursor.lno + 1, pos=0)
    else:
        return Cursor(lno=cursor.lno, pos=cursor.pos + 1)


def _handle_left(lines: List[str], cursor: Cursor) -> Cursor:
    if cursor.lno == 0:
        return Cursor(0, pos=max(0, cursor.pos - 1))
    elif cursor.pos == 0:
        return Cursor(
            lno=cursor.lno - 1,
            pos=len(lines[cursor.lno - 1]) - 1,
        )
    else:
        return Cursor(lno=cursor.lno, pos=cursor.pos - 1)


def _handle_down(lines: List[str], cursor: Cursor) -> Cursor:
    max_y = len(lines) - 1
    if cursor.lno == max_y:
        return Cursor(lno=max_y, pos=len(lines[cursor.lno]) - 1)
    else:
        max_x = len(lines[cursor.lno + 1]) - 1
        return Cursor(lno=cursor.lno + 1, pos=min(max_x, cursor.pos))


def _handle_up(lines: List[str], cursor: Cursor) -> Cursor:
    if cursor.lno == 0:
        return Cursor(0, 0)
    else:
        max_x = len(lines[cursor.lno - 1]) - 1
        return Cursor(lno=cursor.lno - 1, pos=min(max_x, cursor.pos))


def _handle_ctrl_right(lines: List[str], cursor: Cursor) -> Cursor:
    max_x = len(lines[cursor.lno]) - 1
    max_y = len(lines) - 1
    if cursor.pos == max_x and cursor.lno == max_y:
        return cursor
    elif cursor.pos == max_x:
        lno = cursor.lno + 1
        pos = 0
    else:
        lno = cursor.lno
        pos = cursor.pos

    tail = lines[lno][pos:]
    if match := WWB.match(tail):
        return Cursor(lno=lno, pos=pos + match.span()[1])
    else:  # no more words, move to end of line
        return Cursor(lno=lno, pos=len(lines[lno]) - 1)


def _handle_ctrl_left(lines: List[str], cursor: Cursor) -> Cursor:
    if cursor.pos == 0 and cursor.lno == 0:
        return cursor
    elif cursor.pos == 0:
        lno = cursor.lno - 1
        pos = len(lines[lno]) - 1
    else:
        lno = cursor.lno
        pos = cursor.pos

    tail = lines[lno][:pos][::-1]
    if match := WWB.match(tail):
        log(match)
        return Cursor(lno=lno, pos=pos - match.span()[1])
    else:  # no more words, move to start of line
        return Cursor(lno=lno, pos=0)
