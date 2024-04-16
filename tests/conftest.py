from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

import duckdb
import pytest
from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.locale_manager import set_locale
from harlequin.windows_timezone import check_and_install_tzdata

if sys.version_info < (3, 10):
    from importlib_metadata import entry_points
else:
    from importlib.metadata import entry_points


@pytest.fixture(scope="session", autouse=True)
def install_tzdata() -> None:
    if sys.platform == "win32":
        check_and_install_tzdata()


@pytest.fixture(scope="session", autouse=True)
def set_locale_to_enUS() -> None:
    set_locale("en_US.UTF-8")


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
def tiny_sqlite(tmp_path: Path, data_dir: Path) -> Path:
    path_to_data = data_dir / "functional_tests" / "tiny"
    path_to_db = tmp_path / "tiny.sqlite"
    _create_sqlite_db_from_data_dir(path_to_data, path_to_db)
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
def small_sqlite(tmp_path: Path, data_dir: Path) -> Path:
    path_to_data = data_dir / "functional_tests" / "small"
    path_to_db = tmp_path / "small.sqlite"
    _create_sqlite_db_from_data_dir(path_to_data, path_to_db)
    return path_to_db


@pytest.fixture
def duckdb_adapter() -> type[HarlequinAdapter]:
    eps = entry_points(group="harlequin.adapter")
    cls: type[HarlequinAdapter] = eps["duckdb"].load()
    return cls


@pytest.fixture
def sqlite_adapter() -> type[HarlequinAdapter]:
    eps = entry_points(group="harlequin.adapter")
    cls: type[HarlequinAdapter] = eps["sqlite"].load()
    return cls


@pytest.fixture(params=["duckdb", "sqlite"])
def all_adapters(request: pytest.FixtureRequest) -> type[HarlequinAdapter]:
    eps = entry_points(group="harlequin.adapter")
    cls: type[HarlequinAdapter] = eps[request.param].load()
    return cls


@pytest.fixture
def app(duckdb_adapter: type[HarlequinAdapter]) -> Harlequin:
    return Harlequin(duckdb_adapter([":memory:"], no_init=True), connection_hash="foo")


@pytest.fixture
def app_all_adapters(all_adapters: type[HarlequinAdapter]) -> Harlequin:
    return Harlequin(all_adapters([":memory:"], no_init=True), connection_hash="foo")


@pytest.fixture
def app_small_duck(
    duckdb_adapter: type[HarlequinAdapter], small_duck: Path
) -> Harlequin:
    return Harlequin(
        duckdb_adapter([str(small_duck)], no_init=True), connection_hash="small"
    )


@pytest.fixture
def app_small_sqlite(
    sqlite_adapter: type[HarlequinAdapter], small_sqlite: Path
) -> Harlequin:
    return Harlequin(
        sqlite_adapter([str(small_sqlite)], no_init=True), connection_hash="bar"
    )


@pytest.fixture(params=["duckdb", "sqlite"])
def app_all_adapters_small_db(
    request: pytest.FixtureRequest,
    app_small_duck: Harlequin,
    app_small_sqlite: Harlequin,
) -> Harlequin:
    if request.param == "duckdb":
        return app_small_duck
    else:
        return app_small_sqlite


@pytest.fixture
def app_multi_duck(
    duckdb_adapter: type[HarlequinAdapter], tiny_duck: Path, small_duck: Path
) -> Harlequin:
    return Harlequin(
        duckdb_adapter([str(tiny_duck), str(small_duck)], no_init=True),
        connection_hash="multi",
    )


def _create_sqlite_db_from_data_dir(data_dir: Path, db_path: Path) -> None:
    SINGLE_QUOTE = "'"
    DOUBLED_SINGLE_QUOTE = "''"
    conn = sqlite3.connect(str(db_path))
    with open(data_dir / "schema_sqlite.sql") as f:
        ddl = f.read()
    for q in ddl.split(";"):
        conn.execute(q)
    for p in data_dir.iterdir():
        if p.is_file() and p.suffix == ".csv":
            with p.open("r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    quoted = [
                        (
                            val
                            if isinstance(val, (int, float))
                            else f"'{val.replace(SINGLE_QUOTE, DOUBLED_SINGLE_QUOTE)}'"
                        )
                        for val in row
                    ]
                    conn.execute(f"insert into {p.stem} values({', '.join(quoted)})")
                conn.commit()
