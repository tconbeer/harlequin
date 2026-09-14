"""Locating statement boundaries in SQL text.

Both front ends split a script here, through the same tree-sitter grammar and
the same query the Query Editor has always used, so a script run with `-f` and
the same script in the editor cannot disagree about where a statement ends. A
semicolon inside a dollar-quoted body is body content, not a separator: the
query also captures the body's `$tag$` delimiters, and `_separator_offsets()`
drops semicolons between a matching pair.

This module also owns the grammar itself: `captures()` runs a tree-sitter query
over parsed SQL, so the grammar is loaded and each query compiled exactly once,
however many things ask questions of a buffer.

Tree-sitter reports positions as **byte** offsets. Everything this module
returns is in **characters**, because that is what both callers slice with:
`str` for `split()`, and Textual's `Document.get_text_range()` for
`find_separators()`. Owning that conversion once, here, is the point -- doing
it at the call site is what produced the mis-slicing described in
`find_separators()` below.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

import tree_sitter_sql
from tree_sitter import Language, Node, Parser, Query, QueryCursor

SPLITTER_QUERY = """
(";" @semicolon)
(dollar_quote) @dollar_quote
"""
"""Capture semicolons, and the `$tag$` delimiters of dollar-quoted bodies.

Semicolons inside string literals, comments and quoted identifiers are part of
those nodes, so they are not captured. A semicolon inside a dollar-quoted body
is captured -- the grammar parses the body's SQL -- so `_separator_offsets()`
drops it by pairing the delimiters it also captures here.
"""

FOLD_QUERY = """
(comment) @comment
(marginalia) @comment
(literal) @literal
(dollar_quote) @dollar_quote
"""
"""The comments a fold drops, and the spans it may not touch.

`literal` is every quoted thing the grammar knows -- strings and quoted
identifiers, and numbers, which hold no whitespace to collapse. A dollar-quoted
body is the region between a matching pair of delimiters, as in
`SPLITTER_QUERY`.
"""

_WHITESPACE = re.compile(r"\s+")
_LINE_BREAK = re.compile(r"[\r\n\v\f]+")

Point = tuple[int, int]
"""A (row, character column) position in a buffer. Both are 0-indexed."""


@dataclass(frozen=True)
class Statement:
    """A single statement, split out of a submitted script."""

    sql: str
    index: int
    """0-based position in the script this statement was split from."""


_LANGUAGE: Language | None = None
_QUERIES: dict[str, Query] = {}


def _grammar(pattern: str) -> tuple[Language, Query]:
    """Load the grammar and compile `pattern` once, on first use.

    The grammar and one query cost ~16ms together, which nothing that never
    parses SQL should pay -- `hsql --help` being the case that matters. Each
    `Query` is reused across calls, as tree-sitter intends; the `Parser` is not,
    since it holds the tree it last produced.
    """
    global _LANGUAGE
    if _LANGUAGE is None:
        _LANGUAGE = Language(tree_sitter_sql.language())
    query = _QUERIES.get(pattern)
    if query is None:
        query = _QUERIES[pattern] = Query(_LANGUAGE, pattern)
    return _LANGUAGE, query


def captures(text: str, pattern: str) -> dict[str, list[Node]]:
    """Parse `text` and return the nodes each capture in `pattern` matched.

    Nodes keep their tree alive, so they stay valid after this returns. They
    arrive in the order tree-sitter's patterns matched them, which is not the
    order they appear in the buffer; sort on a byte offset to recover that.
    """
    language, query = _grammar(pattern)
    tree = Parser(language).parse(text.encode("utf-8"))
    return QueryCursor(query).captures(tree.root_node)


def _dollar_quoted_regions(tags: list[Node]) -> list[tuple[int, int]]:
    """Return byte spans between matching dollar-quote delimiters.

    Differently named delimiters are content while a body is open.
    """
    regions: list[tuple[int, int]] = []
    opening_tag: Node | None = None
    for tag in sorted(tags, key=lambda node: node.start_byte):
        if opening_tag is None:
            opening_tag = tag
        elif tag.text == opening_tag.text:
            regions.append((opening_tag.end_byte, tag.start_byte))
            opening_tag = None
    return regions


def _separator_offsets(text: str) -> list[int]:
    """Character offsets in `text` just past each statement separator."""
    if ";" not in text:
        # cheap, and the common case for a single statement.
        return []

    matched = captures(text, SPLITTER_QUERY)
    semicolons = sorted(
        (node.start_byte, node.end_byte) for node in matched.get("semicolon", [])
    )
    regions = _dollar_quoted_regions(matched.get("dollar_quote", []))
    byte_offsets: list[int] = []
    region_index = 0
    for semi_start, semi_end in semicolons:
        while region_index < len(regions) and regions[region_index][1] <= semi_start:
            region_index += 1
        if region_index < len(regions):
            region_start, region_end = regions[region_index]
            if region_start <= semi_start and semi_end <= region_end:
                continue  # inside a dollar-quoted body
        byte_offsets.append(semi_end)

    if text.isascii():
        return byte_offsets

    # decode the gaps between offsets rather than the prefix of each, so a
    # script with thousands of statements stays linear in its length.
    encoded = text.encode("utf-8")
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


def fold(sql: str) -> str:
    """One statement on one line, running the same query it ran before.

    A listing prints one line per row, and collapsing every run of whitespace
    is not the way to get one: it pulls the code after a `-- comment` into the
    comment, and it rewrites the whitespace inside a string literal. So the
    comments come out and a literal keeps its own spacing -- which does mean
    the text a `like` matched is not always the text this returns.

    One line is the promise the layouts need, so a literal that spans lines is
    the one thing joined rather than kept: standard SQL has no escape to write
    that newline with.
    """
    if _WHITESPACE.sub(" ", sql).strip() == sql:
        # already one line of single spaces, so it is what was run, and a
        # comment at the end of it has nothing to swallow. No parse, which is
        # the path every statement typed at a shell takes.
        return sql

    matched = captures(sql, FOLD_QUERY)
    spans = _fold_spans(matched)
    encoded = sql.encode("utf-8")
    folded: list[str] = []
    loose: list[str] = []
    cursor = 0
    for start, end, keep in spans:
        loose.append(encoded[cursor:start].decode("utf-8"))
        if keep:
            folded.append(_WHITESPACE.sub(" ", "".join(loose)))
            loose = []
            folded.append(_LINE_BREAK.sub(" ", encoded[start:end].decode("utf-8")))
        elif _would_fuse(encoded, start, end):
            # what the dropped comment leaves behind, so the tokens it sat
            # between do not run together
            loose.append(" ")
        cursor = end
    loose.append(encoded[cursor:].decode("utf-8"))
    folded.append(_WHITESPACE.sub(" ", "".join(loose)))
    return "".join(folded).strip()


def _would_fuse(encoded: bytes, start: int, end: int) -> bool:
    """Whether dropping this span would leave two tokens touching."""
    # `start - 1` at the start of the buffer slices nothing, which is what a
    # comment with no token before it should read as
    before = encoded[start - 1 : start] if start else b""
    return bool(before.strip()) and bool(encoded[end : end + 1].strip())


def _fold_spans(matched: dict[str, list[Node]]) -> list[tuple[int, int, bool]]:
    """Every byte span a fold treats specially, in order and never overlapping.

    `True` is kept verbatim and `False` is dropped. A span inside one already
    taken is skipped: the grammar parses a dollar-quoted body, so a comment in
    one is body content rather than a comment to drop.
    """
    keeps = [(node.start_byte, node.end_byte) for node in matched.get("literal", [])]
    keeps += _dollar_quoted_regions(matched.get("dollar_quote", []))
    drops = [(node.start_byte, node.end_byte) for node in matched.get("comment", [])]
    spans: list[tuple[int, int, bool]] = []
    reached = 0
    for start, end, keep in sorted(
        [(start, end, True) for start, end in keeps]
        + [(start, end, False) for start, end in drops]
    ):
        if start < reached:
            continue
        spans.append((start, end, keep))
        reached = end
    return spans


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
