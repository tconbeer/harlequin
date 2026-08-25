"""Resolving a dotted path into a catalog, and listing the level below it.

The catalog only hands out an item's children, so reaching an item means
fetching its ancestors: one round trip per path segment, plus one for the level
being listed.
"""

from __future__ import annotations

import difflib
import fnmatch
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from harlequin.catalog import CatalogItem, InteractiveCatalogItem
from harlequin.exception import HarlequinCatalogPathError

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinConnection

WILDCARDS = "*?"
"""What makes an unquoted segment a filter rather than a name."""

MUST_QUOTE = '."' + WILDCARDS
"""Characters a segment cannot hold without being written in quotes."""

_TOKEN = re.compile(
    r'"(?P<quoted>(?:[^"]|"")*)"|(?P<bare>[^."]+)|(?P<dot>\.)|(?P<unterminated>")'
)
"""One path token. The last alternative is a quote no closing quote matched."""


@dataclass(frozen=True)
class CatalogPath:
    """A dotted path into a catalog, and an optional filter on the level it names."""

    segments: tuple[str, ...] = ()
    """The items to walk through, outermost first. Empty is the top level."""

    glob: str | None = None
    """A trailing wildcard, applied to the listed children's labels."""

    @classmethod
    def parse(cls, text: str | None) -> "CatalogPath":
        """Read a dotted path, honoring double quotes around a segment.

        Double quotes on every adapter, whatever that database quotes its own
        identifiers with: a path is `spell()`'s output, not SQL.

        Raises: HarlequinCatalogPathError for an unterminated quote, an empty
        segment, or a wildcard anywhere but the last segment.
        """
        if text is None or not text.strip():
            return cls()
        parsed = _split(text)
        *ancestors, (last, last_was_quoted) = parsed
        for value, was_quoted in ancestors:
            if not was_quoted and _has_wildcard(value):
                raise HarlequinCatalogPathError(
                    f"{value!r} contains a wildcard in the middle of a path, "
                    "which is not supported. "
                    "A wildcard is only allowed in the last path segment.",
                    title="Invalid catalog path.",
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

    A trailing wildcard filters the children this fetched, rather than widening
    the walk.

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
    # `parse()` reads a blank path as the top level, so a label that is empty or
    # padded has to come back quoted or it would name the whole catalog
    if segment.strip() == segment and segment and not _needs_quoting(segment):
        return segment
    return '"' + segment.replace('"', '""') + '"'


def _needs_quoting(segment: str) -> bool:
    return any(char in segment for char in MUST_QUOTE)


def _has_wildcard(segment: str) -> bool:
    return any(char in segment for char in WILDCARDS)


def _split(text: str) -> list[tuple[str, bool]]:
    """Each segment of a dotted path, and whether it was written in quotes.

    A `*` in a quoted segment is a character in a name, not a wildcard.
    """
    segments: list[tuple[str, bool]] = []
    value: list[str] = []
    quoted = False
    for token in _TOKEN.finditer(text):
        kind = token.lastgroup
        if kind == "dot":
            segments.append(("".join(value), quoted))
            value, quoted = [], False
        elif kind == "quoted":
            value.append(str(token["quoted"]).replace('""', '"'))
            quoted = True
        elif kind == "bare":
            value.append(str(token["bare"]))
        else:
            raise HarlequinCatalogPathError(
                f"{text!r} opens a quoted path segment and never closes it. "
                'Write a literal quote inside one as "".',
                title="Invalid catalog path.",
            )
    segments.append(("".join(value), quoted))
    for segment, was_quoted in segments:
        if not segment and not was_quoted:
            raise HarlequinCatalogPathError(
                f"{text!r} has an empty path segment. Segments are separated by "
                "a dot, and a name that contains one is written in quotes.",
                title="Invalid catalog path.",
            )
    return segments


def _find(
    segment: str, items: Sequence[CatalogItem], walked: Sequence[str]
) -> CatalogItem:
    """The item in this level labelled `segment`.

    Raises: HarlequinCatalogPathError naming what is there instead.
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
        title="Invalid catalog path.",
    )


def _children_of(item: CatalogItem) -> list[CatalogItem]:
    """One item's children, fetching them the first time it is asked.

    The same two checks the Data Catalog makes before it fetches.
    """
    if item.children:
        return list(item.children)
    if isinstance(item, InteractiveCatalogItem) and not item.loaded:
        children = list(item.fetch_children())
        item.children = children
        item.loaded = True
        return children
    return []
