from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from textual.command import DiscoveryHit, Hit, Hits, SimpleCommand, SimpleProvider
from textual.content import Content

if TYPE_CHECKING:
    from textual.screen import Screen


class PlainTextProvider(SimpleProvider):
    """Discover and highlight commands whose names contain literal text."""

    def __init__(self, screen: Screen[Any], commands: Sequence[SimpleCommand]) -> None:
        super().__init__(screen, list(commands))

    async def discover(self) -> Hits:
        for label, callback, help_text in self._commands:
            yield DiscoveryHit(Content(label), callback, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for label, callback, help_text in self._commands:
            score, offsets = matcher.fuzzy_search.match(matcher.query, label)
            if score:
                content = Content(label)
                for offset in offsets:
                    content = content.stylize(matcher.match_style, offset, offset + 1)
                yield Hit(score, content, callback, help=help_text)
