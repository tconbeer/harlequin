from pathlib import Path

import pytest
from harlequin.tui import Harlequin


@pytest.fixture
def app() -> Harlequin:
    return Harlequin(Path(":memory:"))
