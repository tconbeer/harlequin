from __future__ import annotations

import itertools
import re
from collections.abc import Callable
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

    def __call__(self, prefix: str) -> list[tuple[tuple[str, str], str]]:
        """
        Returns label, value pairs for matching completions.
        """

        def _label(c: HarlequinCompletion) -> tuple[str, str]:
            return (c.label, c.type_label)

        match_val = prefix.lower()
        matches: list[tuple[tuple[str, str], str]] = []

        # Add exact matches
        matches.extend(
            (_label(c), c.value) for c in self.completions if c.match_val == match_val
        )
        # Add prefix matches
        matches.extend(
            (_label(c), c.value)
            for c in self.completions
            if c.match_val.startswith(match_val)
        )
        # Only add fuzzy matches if there are not enough exact matches
        if len(matches) < 20:
            matches.extend(
                (_label(c), c.value)
                for c in self._fuzzy_match(match_val, self.completions)
            )

        return self._dedupe_labels(matches)

    def update_catalog(self, catalog: Catalog) -> None:
        self._catalog_completions = build_catalog_completions(catalog=catalog)
        self.completions = self._merge_completions(
            self._keyword_completions,
            self._function_completions,
            self._catalog_completions,
            self._extra_completions,
        )

    def extend_catalog(self, parent: CatalogItem, items: list[CatalogItem]) -> None:
        # TODO: dedupe/merge on the parent's unique key, so we can load items from
        # a cache and update them later when they are lazy-loaded.
        new_completions = _build_children_completions(items=items, context=parent.label)
        self._catalog_completions.extend(new_completions)
        self.completions = self._merge_completions(
            self._keyword_completions,
            self._function_completions,
            self._catalog_completions,
            self._extra_completions,
        )

    @staticmethod
    def _fuzzy_match(
        match_val: str, completions: list[HarlequinCompletion]
    ) -> list[HarlequinCompletion]:
        regex_base = ".{0,2}?".join(f"({re.escape(c)})" for c in match_val)
        regex = "^.*" + regex_base + ".*$"
        match_regex = re.compile(regex, re.IGNORECASE)
        matches = [c for c in completions if match_regex.match(c.match_val)]

        # Sort in ascending length.
        # I am assuming here that more insertions are less likely to be
        # the "right" match.
        matches.sort(key=lambda c: len(c.match_val))
        return matches

    @staticmethod
    def _merge_completions(
        *completion_lists: list[HarlequinCompletion],
    ) -> list[HarlequinCompletion]:
        return [c for c in sorted(itertools.chain(*completion_lists))]

    @staticmethod
    def _dedupe_labels(
        matches: Iterable[tuple[tuple[str, str], str]],
    ) -> list[tuple[tuple[str, str], str]]:
        uniques: set[tuple[str, str]] = set()
        uniques_add = uniques.add
        deduped = [m for m in matches if not (m[0] in uniques or uniques_add(m[0]))]
        return deduped


class MemberCompleter(WordCompleter):
    def __call__(self, prefix: str) -> list[tuple[tuple[str, str], str]]:
        """
        Returns label, value pairs for matching completions.
        """

        def _label(
            c: HarlequinCompletion, value_prefix: str, quote_char: str
        ) -> tuple[str, str]:
            return (f"{value_prefix}{quote_char}{c.label}", c.type_label)

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
            f"{w}{sep}" for w, sep in zip([*others, context], separators, strict=False)
        )

        context_completions = [
            c for c in self.completions if c.context == match_context
        ]

        matches: list[tuple[tuple[str, str], str]] = []
        # Add exact matches
        matches.extend(
            self.format_completion(c, quote_char, value_prefix, _label)
            for c in context_completions
            if c.match_val == match_val
        )

        # Add prefix matches
        matches.extend(
            self.format_completion(c, quote_char, value_prefix, _label)
            for c in context_completions
            if c.match_val.startswith(match_val)
        )

        # Only add fuzzy matches if there are not enough exact matches
        if len(matches) < 20:
            matches.extend(
                self.format_completion(c, quote_char, value_prefix, _label)
                for c in self._fuzzy_match(match_val, context_completions)
            )

        return self._dedupe_labels(matches)

    @staticmethod
    def format_completion(
        completion: HarlequinCompletion,
        quote_char: str,
        value_prefix: str,
        label_fn: Callable[[HarlequinCompletion, str, str], tuple[str, str]],
    ) -> tuple[tuple[str, str], str]:
        return (
            label_fn(completion, value_prefix, quote_char),
            f"{value_prefix}{quote_char}{completion.value}",
        )

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
) -> tuple[WordCompleter, MemberCompleter]:
    keyword_completions = get_keywords()
    function_completions = get_functions()
    catalog_completions = build_catalog_completions(catalog)
    return WordCompleter(
        keyword_completions=keyword_completions,
        function_completions=function_completions,
        catalog_completions=catalog_completions,
        extra_completions=extra_completions,
    ), MemberCompleter(
        keyword_completions=keyword_completions,
        function_completions=function_completions,
        catalog_completions=catalog_completions,
        extra_completions=extra_completions,
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
