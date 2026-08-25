"""The identifiers a user has typed into a buffer, for the autocompleters.

A buffer names things the catalog does not -- a CTE, an alias, a column of a
table that has not been created yet -- and the things it does name are the ones
the user is most likely to want next. `find_symbols()` reads both out of the
tree-sitter parse, so a mid-edit buffer full of syntax errors still yields the
identifiers around the error.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

SYMBOL_QUERY = """
(identifier) @identifier
[(object_reference) (field)] @reference
"""
"""Capture every identifier, and every dotted reference that relates two of them.

Keywords are their own node types, so `select` and `from` are not identifiers;
function names are, and are deduped against the function completions.
"""

QUOTE_CHARS = "\"'`[]"


@dataclass(frozen=True)
class BufferSymbols:
    """The identifiers in a buffer, in the order they first appear."""

    names: tuple[str, ...] = ()
    members: tuple[tuple[str, str], ...] = ()
    """One `(context, name)` pair per `context.name` written in the buffer."""


NO_SYMBOLS = BufferSymbols()


def find_symbols(text: str) -> BufferSymbols:
    """Extract every identifier in `text`, and each `context.name` pair among them.

    Names are deduplicated caselessly, keeping the spelling that appears first.
    """
    if not text.strip():
        return NO_SYMBOLS

    # deferred: every adapter imports this package, and only the Query Editor
    # ever parses SQL with it.
    from harlequin.statements import captures

    matched = captures(text, SYMBOL_QUERY)

    names: dict[str, str] = {}
    for node in sorted(matched.get("identifier", []), key=lambda n: n.start_byte):
        name = _identifier(node)
        if name:
            names.setdefault(name.casefold(), name)

    members: dict[tuple[str, str], tuple[str, str]] = {}
    for node in sorted(matched.get("reference", []), key=lambda n: n.start_byte):
        parts = _dotted_parts(node)
        for context, name in pairwise(parts):
            members.setdefault((context.casefold(), name.casefold()), (context, name))

    return BufferSymbols(names=tuple(names.values()), members=tuple(members.values()))


def _identifier(node: Node) -> str:
    """The text of an identifier node, without whatever quoted it."""
    if node.text is None:
        return ""
    return node.text.decode("utf-8").strip(QUOTE_CHARS)


def _dotted_parts(node: Node) -> list[str]:
    """The identifiers of a dotted reference, left to right.

    A three-part reference nests: `db.sch.tbl` is an `object_reference` holding
    `db` and `sch` inside the one holding `tbl`.
    """
    parts: list[str] = []
    for child in node.children:
        if child.type == "identifier":
            name = _identifier(child)
            if name:
                parts.append(name)
        elif child.type == "object_reference":
            parts.extend(_dotted_parts(child))
    return parts
