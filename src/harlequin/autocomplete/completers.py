from __future__ import annotations

import itertools
import re
from typing import Iterable

from harlequin.autocomplete.completion import HarlequinCompletion
from harlequin.autocomplete.constants import get_functions, get_keywords
from harlequin.catalog import Catalog, CatalogItem

SEPARATOR_PROG = re.compile(r"\.|::?")
ANY_QUOTE_PROG = re.compile(r"\"|'|`")


class WordCompleter:
    def __init__(
        self,
        keyword_completions: list[HarlequinCompletion],
        function_completions: list[HarlequinCompletion],
        catalog_completions: list[HarlequinCompletion],
        extra_completions: list[HarlequinCompletion] | None = None,
        type_color: str = "#888888",
    ) -> None:
        self._keyword_completions = keyword_completions
        self._function_completions = function_completions
        self._catalog_completions = catalog_completions
        self._extra_completions = extra_completions or []
        self.completions: list[HarlequinCompletion] = self._merge_completions(
            self._keyword_completions,
            self._function_completions,
            self._catalog_completions,
            self._extra_completions,
        )
        self.type_color = type_color

    def __call__(self, prefix: str) -> list[tuple[str, str]]:
        """
        Returns label, value pairs for matching completions.
        """

        def _label(c: HarlequinCompletion) -> str:
            return f"{c.label} [{self.type_color}]{c.type_label}[/]"

        match_val = prefix.lower()

        exact_matches = [
            (_label(c), c.value) for c in self.completions if c.match_val == match_val
        ]
        matches = [
            (_label(c), c.value)
            for c in self.completions
            if c.match_val.startswith(match_val)
        ]
        return self._dedupe_labels((*exact_matches, *matches))

    def update_catalog(self, catalog: Catalog) -> None:
        self._catalog_completions = build_catalog_completions(catalog=catalog)
        self.completions = self._merge_completions(
            self._keyword_completions,
            self._function_completions,
            self._catalog_completions,
            self._extra_completions,
        )

    @staticmethod
    def _merge_completions(
        *completion_lists: list[HarlequinCompletion],
    ) -> list[HarlequinCompletion]:
        return [c for c in sorted(itertools.chain(*completion_lists))]

    @staticmethod
    def _dedupe_labels(matches: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        uniques: set[str] = set()
        uniques_add = uniques.add
        deduped = [m for m in matches if not (m[0] in uniques or uniques_add(m[0]))]
        return deduped


class MemberCompleter(WordCompleter):
    def __call__(self, prefix: str) -> list[tuple[str, str]]:
        """
        Returns label, value pairs for matching completions.
        """

        def _label(c: HarlequinCompletion) -> str:
            return f"{c.label} [{self.type_color}]{c.type_label}[/]"

        try:
            *others, context, item_prefix = SEPARATOR_PROG.split(prefix)
        except ValueError:
            return []
        else:
            quote_match = ANY_QUOTE_PROG.match(item_prefix)
            if quote_match is not None:
                quote_char = quote_match.group(0)
                match_val = item_prefix[1:].lower()
            else:
                quote_char = ""
                match_val = item_prefix.lower()
            match_context = context.strip("'`\"").lower()
            separators = SEPARATOR_PROG.findall(prefix)
        value_prefix = "".join(
            f"{w}{sep}" for w, sep in zip([*others, context], separators)
        )
        exact_matches = [
            (
                f"{value_prefix}{quote_char}{_label(c)}",
                f"{value_prefix}{quote_char}{c.value}",
            )
            for c in self.completions
            if c.match_val == match_val and c.context == match_context
        ]
        matches = [
            (
                f"{value_prefix}{quote_char}{_label(c)}",
                f"{value_prefix}{quote_char}{c.value}",
            )
            for c in self.completions
            if c.match_val.startswith(match_val) and c.context == match_context
        ]
        return self._dedupe_labels((*exact_matches, *matches))

    @staticmethod
    def _merge_completions(
        *completion_lists: list[HarlequinCompletion],
    ) -> list[HarlequinCompletion]:
        return [
            c
            for c in sorted(itertools.chain(*completion_lists))
            if c.context is not None
        ]


def completer_factory(
    catalog: Catalog,
    extra_completions: list[HarlequinCompletion] | None = None,
    type_color: str = "#888888",
) -> tuple[WordCompleter, MemberCompleter]:
    keyword_completions = get_keywords()
    function_completions = get_functions()
    catalog_completions = build_catalog_completions(catalog)
    return WordCompleter(
        keyword_completions=keyword_completions,
        function_completions=function_completions,
        catalog_completions=catalog_completions,
        extra_completions=extra_completions,
        type_color=type_color,
    ), MemberCompleter(
        keyword_completions=keyword_completions,
        function_completions=function_completions,
        catalog_completions=catalog_completions,
        extra_completions=extra_completions,
        type_color=type_color,
    )


def build_catalog_completions(catalog: Catalog) -> list[HarlequinCompletion]:
    return _build_children_completions(catalog.items)


def _build_children_completions(
    items: list[CatalogItem], context: str | None = None, depth: int = 0
) -> list[HarlequinCompletion]:
    completions: list[HarlequinCompletion] = []
    for item in items:
        completions.append(
            HarlequinCompletion(
                label=item.label,
                type_label=item.type_label,
                value=item.label,
                priority=500 + depth,
                context=context,
            )
        )
        completions.extend(
            _build_children_completions(
                item.children, context=item.label, depth=depth + 1
            )
        )
    return completions
