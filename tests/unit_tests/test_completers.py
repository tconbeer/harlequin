import json
from pathlib import Path

import pytest

from harlequin.autocomplete.completers import WordCompleter
from harlequin.autocomplete.completion import HarlequinCompletion
from harlequin.catalog import CatalogItem


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
