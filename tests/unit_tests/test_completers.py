import json
from pathlib import Path

import pytest

from harlequin.autocomplete.completers import WordCompleter
from harlequin.autocomplete.completion import HarlequinCompletion


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
    assert completions[0][1] == "select"
    assert all(x[1].startswith("se") for x in completions)


def test_completer_fuzzy_match(iris_completer: WordCompleter) -> None:
    completions = iris_completer("width")
    labels = {x[1] for x in completions}

    assert "petal_width" in labels
    assert "sepal_width" in labels
    assert len(labels) == 2
