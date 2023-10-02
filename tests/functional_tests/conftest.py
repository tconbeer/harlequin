from pathlib import Path

import duckdb
import pytest
from harlequin import Harlequin


@pytest.fixture(autouse=True)
def mock_user_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr("harlequin.cache.user_cache_dir", lambda **_: tmp_path)
    return tmp_path


@pytest.fixture
def tiny_db(tmp_path: Path, data_dir: Path) -> Path:
    """
    Creates a duckdb database file from the contents of
    data_dir/functional_tests/tiny
    """
    path_to_data = data_dir / "functional_tests" / "tiny"
    path_to_db = tmp_path / "tiny.db"
    conn = duckdb.connect(str(path_to_db))
    conn.execute(f"import database '{path_to_data}';")
    return path_to_db


@pytest.fixture
def small_db(tmp_path: Path, data_dir: Path) -> Path:
    """
    Creates a duckdb database file from the contents of
    data_dir/functional_tests/small
    """
    path_to_data = data_dir / "functional_tests" / "small"
    path_to_db = tmp_path / "small.db"
    conn = duckdb.connect(str(path_to_db))
    conn.execute(f"import database '{path_to_data}';")
    return path_to_db


@pytest.fixture
def app() -> Harlequin:
    return Harlequin([":memory:"])


@pytest.fixture
def app_small_db(small_db: Path) -> Harlequin:
    return Harlequin([str(small_db)])


@pytest.fixture
def app_multi_db(tiny_db: Path, small_db: Path) -> Harlequin:
    return Harlequin([str(tiny_db), str(small_db)])
