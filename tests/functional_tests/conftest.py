import shutil
from pathlib import Path

import pytest
from harlequin import Harlequin


@pytest.fixture(autouse=True)
def mock_user_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr("harlequin.cache.user_cache_dir", lambda **_: tmp_path)
    return tmp_path


@pytest.fixture
def tiny_db(tmp_path: Path, data_dir: Path) -> Path:
    """
    Copies data/functional_tests/tiny.db to a
    tmp dir and returns the path to the copy.
    """
    original = data_dir / "functional_tests" / "tiny.db"
    return Path(shutil.copy(original, tmp_path))


@pytest.fixture
def small_db(tmp_path: Path, data_dir: Path) -> Path:
    """
    Copies data/functional_tests/tiny.db to a
    tmp dir and returns the path to the copy.
    """
    original = data_dir / "functional_tests" / "small.db"
    return Path(shutil.copy(original, tmp_path))


@pytest.fixture
def app() -> Harlequin:
    return Harlequin([":memory:"])


@pytest.fixture
def app_small_db(small_db: Path) -> Harlequin:
    return Harlequin([str(small_db)])


@pytest.fixture
def app_multi_db(tiny_db: Path, small_db: Path) -> Harlequin:
    return Harlequin([str(tiny_db), str(small_db)])
