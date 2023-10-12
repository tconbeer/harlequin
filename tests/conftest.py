from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

import duckdb
import pytest
from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter


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
    path_to_data = data_dir / "functional_tests" / "tiny"
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
    path_to_data = data_dir / "functional_tests" / "small"
    path_to_db = tmp_path / "small.db"
    conn = duckdb.connect(str(path_to_db))
    conn.execute(f"import database '{path_to_data}';")
    return path_to_db


@pytest.fixture
def duckdb_adapter() -> type[HarlequinAdapter]:
    eps = entry_points(group="harlequin.adapter")  # type: ignore
    cls: type[HarlequinAdapter] = eps["duckdb"].load()  # type: ignore
    return cls


@pytest.fixture
def app(duckdb_adapter: type[HarlequinAdapter]) -> Harlequin:
    return Harlequin(duckdb_adapter([":memory:"], no_init=True))


@pytest.fixture
def app_small_duck(
    duckdb_adapter: type[HarlequinAdapter], small_duck: Path
) -> Harlequin:
    return Harlequin(duckdb_adapter([str(small_duck)], no_init=True))


@pytest.fixture
def app_multi_duck(
    duckdb_adapter: type[HarlequinAdapter], tiny_duck: Path, small_duck: Path
) -> Harlequin:
    return Harlequin(duckdb_adapter([str(tiny_duck), str(small_duck)], no_init=True))
