from pathlib import Path

import pytest


@pytest.fixture
def data_dir() -> Path:
    here = Path(__file__)
    return here.parent / "data"
