from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def data_dir() -> Path:
    here = Path(__file__)
    return here.parent / "data"


@pytest.fixture
def tiny_duck(tmp_path: Path, data_dir: Path) -> Path:
    """
    Creates a duckdb database file from the contents of
    data_dir/functional_tests/tiny
    """
    path_to_data = data_dir / "tiny"
    path_to_db = tmp_path / "tiny.db"
    conn = duckdb.connect(str(path_to_db))
    conn.execute(f"import database '{path_to_data}';")
    return path_to_db


@pytest.fixture
def small_duck(tmp_path: Path, data_dir: Path) -> Path:
    """
    Creates a duckdb database file from the contents of
    data_dir/functional_tests/small
    """
    path_to_data = data_dir / "small"
    path_to_db = tmp_path / "small.db"
    conn = duckdb.connect(str(path_to_db))
    conn.execute(f"import database '{path_to_data}';")
    return path_to_db
