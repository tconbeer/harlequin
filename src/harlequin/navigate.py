"""Standing somewhere in a catalog, and listing what is directly under it.

The catalog contract is lazy in one direction only: `get_catalog()` returns the
top level, and every level below it is a `fetch_children()` call on the item you
are already holding. So there is no way to *reach* an item except by fetching
its ancestors, and this module is that walk -- one round trip per path segment,
plus one for the level being listed.

**One level, always.** A listing is the children of the item a path names, and
nothing deeper, because what recursion costs depends entirely on where it
starts: a second level below a database is one call per database, and a second
level below a schema is one call per relation -- 401 calls on a schema with 400
of them, on the call an agent most wants to make.

**A trailing wildcard is sugar; an interior one is a different question.**
`analytics.ord*` filters one resolved parent's children in the client and stays
one round trip. `*.orders` cannot be answered without fetching every candidate
level, so it is refused here rather than quietly walked.

Paths are **positional segments**, and what a level means belongs to the adapter:
DuckDB and Postgres are database.schema.relation.column, SQLite is
database.relation.column, and BigQuery's project.dataset.table is four levels
wearing different words. Nothing in the contract says how deep a catalog goes, so
this module counts segments and lets each item's own type label say what it is.
"""

from __future__ import annotations

import difflib
import fnmatch
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from harlequin.catalog import CatalogItem, InteractiveCatalogItem
from harlequin.exception import HarlequinCatalogPathError

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinConnection

WILDCARDS = "*?"
"""What makes a segment a filter rather than a name, outside of quotes."""


@dataclass(frozen=True)
class CatalogPath:
    """Where in a catalog to stand, and optionally which children to keep."""

    segments: tuple[str, ...] = ()
    """The items to walk through, outermost first. Empty is the top level."""

    glob: str | None = None
    """A trailing wildcard, applied to the listed children's labels."""

    @classmethod
    def parse(cls, text: str | None) -> "CatalogPath":
        """Read a dotted path, honoring double quotes around a segment.

        Quoting is how a label containing a dot, or a literal `*`, is spelled --
        the same escape SQL uses, so a `query_name` an adapter emitted can be
        pasted back in as a path.

        Raises: HarlequinCatalogPathError for an unterminated quote, an empty
        segment, or a wildcard anywhere but at the end.
        """
        if text is None or not text.strip():
            return cls()
        parsed = _split(text)
        *ancestors, (last, last_was_quoted) = parsed
        for value, was_quoted in ancestors:
            if not was_quoted and _has_wildcard(value):
                raise HarlequinCatalogPathError(
                    f"{value!r} is a wildcard in the middle of a path, which "
                    "cannot be answered without fetching every level it could "
                    "match. A wildcard is only allowed on the last segment.",
                    title="Harlequin could not read that catalog path.",
                )
        if not last_was_quoted and _has_wildcard(last):
            return cls(segments=tuple(value for value, _ in ancestors), glob=last)
        return cls(segments=tuple(value for value, _ in parsed))


@dataclass
class Listing:
    """One level of a catalog: an item, and the children directly under it."""

    parent: CatalogItem | None = None
    """The item the path named, or None at the top level."""

    items: list[CatalogItem] = field(default_factory=list)


def resolve(connection: "HarlequinConnection", path: CatalogPath) -> CatalogItem | None:
    """The item `path` names, or None for the top of the catalog.

    Costs one round trip per segment: the catalog can only hand out an item's
    children, so every ancestor is fetched on the way past.

    Raises: HarlequinCatalogPathError if a segment names nothing.
    """
    item: CatalogItem | None = None
    for depth, segment in enumerate(path.segments):
        level = (
            _children_of(item) if item is not None else connection.get_catalog().items
        )
        item = _find(segment, level, walked=path.segments[:depth])
    return item


def list_children(connection: "HarlequinConnection", path: CatalogPath) -> Listing:
    """The children of the item `path` names, and that item.

    One round trip more than `resolve()`, and no more than that: a trailing
    wildcard filters the children this fetched rather than widening the walk.

    Raises: HarlequinCatalogPathError if a segment names nothing.
    """
    parent = resolve(connection, path)
    items = (
        _children_of(parent) if parent is not None else connection.get_catalog().items
    )
    if path.glob is not None:
        # `fnmatchcase`, not `fnmatch`: the latter normalizes case on Windows,
        # and a listing that depends on the platform is not a listing a script
        # can rely on.
        items = [item for item in items if fnmatch.fnmatchcase(item.label, path.glob)]
    return Listing(parent=parent, items=list(items))


def spell(segments: Sequence[str]) -> str:
    """Segments as a path `parse()` reads back as the same segments."""
    return ".".join(_spell_segment(segment) for segment in segments)


def _spell_segment(segment: str) -> str:
    if segment and not any(char in segment for char in '."' + WILDCARDS):
        return segment
    return '"' + segment.replace('"', '""') + '"'


def _has_wildcard(segment: str) -> bool:
    return any(char in segment for char in WILDCARDS)


def _split(text: str) -> list[tuple[str, bool]]:
    """Each segment of a dotted path, and whether it was written in quotes.

    Quoted is carried alongside the value because it is what decides whether a
    `*` in it is a wildcard or a character in a name.
    """
    segments: list[tuple[str, bool]] = []
    value: list[str] = []
    quoted = False
    in_quotes = False
    position = 0
    while position < len(text):
        char = text[position]
        if in_quotes:
            if char != '"':
                value.append(char)
            elif text[position + 1 : position + 2] == '"':
                value.append('"')
                position += 1
            else:
                in_quotes = False
        elif char == '"':
            in_quotes = quoted = True
        elif char == ".":
            segments.append(("".join(value), quoted))
            value, quoted = [], False
        else:
            value.append(char)
        position += 1
    if in_quotes:
        raise HarlequinCatalogPathError(
            f"{text!r} opens a quoted path segment and never closes it. "
            'Write a literal quote inside one as "".',
            title="Harlequin could not read that catalog path.",
        )
    segments.append(("".join(value), quoted))
    for segment, was_quoted in segments:
        if not segment and not was_quoted:
            raise HarlequinCatalogPathError(
                f"{text!r} has an empty path segment. Segments are separated by "
                "a dot, and a name that contains one is written in quotes.",
                title="Harlequin could not read that catalog path.",
            )
    return segments


def _find(
    segment: str, items: Sequence[CatalogItem], walked: Sequence[str]
) -> CatalogItem:
    """The item in this level labelled `segment`.

    Raises: HarlequinCatalogPathError naming what is there instead. A path an
    agent guessed at is the common case, so the error is the one chance to
    answer the question it was asking.
    """
    for item in items:
        if item.label == segment:
            return item
    where = f"under {spell(walked)}" if walked else "at the top of the catalog"
    labels = [item.label for item in items]
    close = difflib.get_close_matches(segment, labels, n=3)
    if close:
        hint = f" Did you mean {', '.join(close)}?"
    elif labels:
        shown = ", ".join(labels[:5])
        hint = f" That level has {len(labels)}: {shown}" + (
            ", ..." if len(labels) > 5 else ""
        )
    else:
        hint = " That level has nothing under it."
    raise HarlequinCatalogPathError(
        f"There is no {segment!r} {where}.{hint}",
        title="Harlequin could not find that catalog path.",
    )


def _children_of(item: CatalogItem) -> list[CatalogItem]:
    """One item's children, fetching them if this is the first time it was asked.

    The same two questions the Data Catalog asks before it fetches: children it
    already holds are the answer, and an item that says it is loaded has none.
    """
    if item.children:
        return list(item.children)
    if isinstance(item, InteractiveCatalogItem) and not item.loaded:
        children = list(item.fetch_children())
        item.children = children
        item.loaded = True
        return children
    return []
