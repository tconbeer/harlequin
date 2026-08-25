import json
from pathlib import Path

import pytest

from harlequin.autocomplete.completers import (
    MemberCompleter,
    WordCompleter,
    completer_factory,
)
from harlequin.autocomplete.completion import HarlequinCompletion
from harlequin.autocomplete.symbols import BufferSymbols
from harlequin.catalog import Catalog, CatalogItem


@pytest.fixture
def iris_completer(data_dir: Path) -> WordCompleter:
    source = data_dir / "unit_tests" / "completions" / "iris_db.json"
    with open(source, "r") as f:
        data = f.read()
    completions = [HarlequinCompletion(**x) for x in json.loads(data)]
    completer = WordCompleter([], [], [], [])
    completer.completions = completions
    return completer


def test_completer_fixed_first(iris_completer: WordCompleter) -> None:
    completions = iris_completer("se")
    assert completions[0][1] == "select", completions[0][1]


def test_completer_fuzzy_match(iris_completer: WordCompleter) -> None:
    completions = iris_completer("width")
    labels = {x[1] for x in completions}

    assert "petal_width" in labels
    assert "sepal_width" in labels
    assert len(labels) == 2


@pytest.fixture
def parent_item() -> CatalogItem:
    return CatalogItem(
        qualified_identifier='"main"."foo"',
        query_name='"main"."foo"',
        label="foo",
        type_label="t",
        children=[
            CatalogItem(
                qualified_identifier='"main"."foo"."bar"',
                query_name='"bar"',
                label="bar",
                type_label="#",
            )
        ],
    )


def test_extend_catalog_merges_by_default(parent_item: CatalogItem) -> None:
    completer = WordCompleter([], [], [], [])
    completer.extend_catalog(parent=parent_item, items=parent_item.children)
    assert any(c.label == "bar" for c in completer.completions)


def test_extend_catalog_defer_merge(parent_item: CatalogItem) -> None:
    completer = WordCompleter([], [], [], [])
    completer.extend_catalog(
        parent=parent_item, items=parent_item.children, defer_merge=True
    )
    # the new items are staged but not yet visible
    assert not any(c.label == "bar" for c in completer.completions)
    completer.merge()
    assert any(c.label == "bar" for c in completer.completions)


@pytest.fixture
def catalog() -> Catalog:
    def _table(label: str, columns: list[str]) -> CatalogItem:
        return CatalogItem(
            qualified_identifier=f'"{label}"',
            query_name=f'"{label}"',
            label=label,
            type_label="t",
            children=[
                CatalogItem(
                    qualified_identifier=f'"{label}"."{column}"',
                    query_name=f'"{column}"',
                    label=column,
                    type_label="#",
                )
                for column in columns
            ],
        )

    return Catalog(
        items=[
            _table("alpha", ["col_a", "shared"]),
            _table("beta", ["col_b", "shared"]),
        ]
    )


@pytest.fixture
def word_completer(catalog: Catalog) -> WordCompleter:
    word, _ = completer_factory(catalog=catalog)
    return word


@pytest.fixture
def member_completer(catalog: Catalog) -> MemberCompleter:
    _, member = completer_factory(catalog=catalog)
    return member


def test_buffer_symbol_is_offered(word_completer: WordCompleter) -> None:
    assert not any(label == "my_cte" for (label, _), _ in word_completer("my_c"))
    word_completer.update_buffer_symbols(BufferSymbols(names=("my_cte",)))
    assert word_completer("my_c")[0] == (("my_cte", "buf"), "my_cte")


def test_buffer_symbol_does_not_complete_to_itself(
    word_completer: WordCompleter,
) -> None:
    word_completer.update_buffer_symbols(BufferSymbols(names=("my_cte", "my_cte_2")))
    labels = {label for (label, _), _ in word_completer("my_cte")}
    assert "my_cte" not in labels
    assert "my_cte_2" in labels


def test_buffer_symbol_is_not_duplicated(word_completer: WordCompleter) -> None:
    word_completer.update_buffer_symbols(BufferSymbols(names=("alpha",)))
    assert [label for label, _ in word_completer("alpha")] == [("alpha", "t")]


def test_catalog_item_in_buffer_ranks_first(word_completer: WordCompleter) -> None:
    assert word_completer("al")[0][0] != ("alpha", "t")
    word_completer.update_buffer_symbols(BufferSymbols(names=("alpha",)))
    assert word_completer("al")[0][0] == ("alpha", "t")


def test_completion_whose_context_is_in_buffer_ranks_higher(
    word_completer: WordCompleter,
) -> None:
    def _rank_of(label: str) -> int:
        return [x[0][0] for x in word_completer("col")].index(label)

    assert _rank_of("col_a") < _rank_of("col_b")
    word_completer.update_buffer_symbols(BufferSymbols(names=("beta",)))
    assert _rank_of("col_b") < _rank_of("col_a")


def test_member_completions_come_from_the_buffer(
    member_completer: MemberCompleter,
) -> None:
    assert member_completer("t.") == []
    member_completer.update_buffer_symbols(
        BufferSymbols(names=("alpha", "t", "extra_col"), members=(("t", "extra_col"),))
    )
    assert member_completer("t.") == [(("t.extra_col", "buf"), "t.extra_col")]


def test_member_completions_from_the_buffer_are_not_duplicated(
    member_completer: MemberCompleter,
) -> None:
    member_completer.update_buffer_symbols(
        BufferSymbols(names=("alpha", "col_a"), members=(("alpha", "col_a"),))
    )
    assert [label for label, _ in member_completer("alpha.col_a")] == [
        ("alpha.col_a", "#")
    ]


def test_buffer_symbols_survive_a_catalog_update(
    word_completer: WordCompleter, catalog: Catalog
) -> None:
    word_completer.update_buffer_symbols(BufferSymbols(names=("my_cte",)))
    word_completer.update_catalog(catalog)
    assert word_completer("my_c")[0] == (("my_cte", "buf"), "my_cte")
