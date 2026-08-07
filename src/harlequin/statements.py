"""Locating statement boundaries in SQL text.

Both front ends split a script here, through the same tree-sitter grammar and
the same one-line query the Query Editor has always used, so a script run with
`-f` and the same script in the editor cannot disagree about where a statement
ends.

Tree-sitter reports positions as **byte** offsets. Everything this module
returns is in **characters**, because that is what both callers slice with:
`str` for `split()`, and Textual's `Document.get_text_range()` for
`find_separators()`. Owning that conversion once, here, is the point -- doing
it at the call site is what produced the mis-slicing described in
`find_separators()` below.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

import tree_sitter_sql
from tree_sitter import Language, Parser, Query, QueryCursor

SEMICOLON_QUERY = '(";" @semicolon)'
"""Capture every semicolon the grammar considers a statement separator.

Semicolons inside string literals, comments and quoted identifiers are part of
those nodes, so they are not captured.
"""

Point = tuple[int, int]
"""A (row, character column) position in a buffer. Both are 0-indexed."""


@dataclass(frozen=True)
class Statement:
    """A single statement, split out of a submitted script."""

    sql: str
    index: int
    """0-based position in the script this statement was split from."""


_LANGUAGE: Language | None = None
_QUERY: Query | None = None


def _grammar() -> tuple[Language, Query]:
    """Load the grammar and prepare the query once, on first use.

    Together they cost ~16ms, which nothing that never splits SQL should pay --
    `hsql --help` being the case that matters. The `Query` is reused across
    calls, as tree-sitter intends; the `Parser` is not, since it holds the tree
    it last produced.
    """
    global _LANGUAGE, _QUERY
    if _LANGUAGE is None or _QUERY is None:
        _LANGUAGE = Language(tree_sitter_sql.language())
        _QUERY = Query(_LANGUAGE, SEMICOLON_QUERY)
    return _LANGUAGE, _QUERY


def _separator_offsets(text: str) -> list[int]:
    """Character offsets in `text` just past each statement separator."""
    if ";" not in text:
        # cheap, and the common case for a single statement.
        return []

    language, query = _grammar()
    encoded = text.encode("utf-8")
    tree = Parser(language).parse(encoded)
    captures = QueryCursor(query).captures(tree.root_node)
    # tree-sitter captures nodes in the order its patterns match them, which is
    # not the order they appear in the buffer.
    byte_offsets = sorted(node.end_byte for node in captures.get("semicolon", []))

    if text.isascii():
        return byte_offsets

    # decode the gaps between offsets rather than the prefix of each, so a
    # script with thousands of statements stays linear in its length.
    offsets: list[int] = []
    byte_cursor = 0
    char_cursor = 0
    for byte_offset in byte_offsets:
        char_cursor += len(encoded[byte_cursor:byte_offset].decode("utf-8"))
        byte_cursor = byte_offset
        offsets.append(char_cursor)
    return offsets


def split(text: str) -> list[Statement]:
    """Split a script into its statements, in order.

    Each statement's `sql` is stripped of surrounding whitespace and keeps its
    trailing semicolon; statements that are empty after stripping are dropped,
    so trailing separators and blank lines do not produce empty statements.
    """
    statements: list[Statement] = []
    start = 0
    for end in [*_separator_offsets(text), len(text)]:
        sql = text[start:end].strip()
        if sql:
            statements.append(Statement(sql=sql, index=len(statements)))
        start = end
    return statements


def find_separators(text: str) -> list[Point]:
    """Locate each statement separator, as a (row, character column) `Point`.

    The point sits immediately *past* the semicolon, so it is the end of the
    statement it terminates.

    Rows and columns are those of `str.splitlines()`, which is how Textual's
    `Document` splits a buffer -- so a `Point` can be handed straight to
    `Document.get_text_range()`. That is the whole reason this returns
    characters: tree-sitter's own `Point.column` is a byte offset, and feeding
    one to `get_text_range()` shifts the cut by one position per non-ASCII
    character earlier on the line, splitting `select '日本語';select 2` into
    `select '日本語';select` and `2`.
    """
    offsets = _separator_offsets(text)
    if not offsets:
        return []

    lines = text.splitlines(keepends=True)
    # `line_starts[row]` is the character offset of the start of that row; the
    # final entry is the end of the buffer.
    line_starts = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line))

    points: list[Point] = []
    for offset in offsets:
        # a separator is never the first character of a row -- the character
        # before it is a semicolon -- so it only lands on a `line_starts` entry
        # when it is at the very end of the buffer.
        row = min(bisect_right(line_starts, offset) - 1, len(lines) - 1)
        points.append((row, offset - line_starts[row]))
    return points
