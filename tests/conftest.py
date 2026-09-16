from __future__ import annotations

import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.catalog_cache import HISTORY_CACHE_VERSION
from harlequin.locale_manager import set_locale
from harlequin.windows_timezone import download_tzdata, find_tzdata

if sys.version_info < (3, 10):
    from importlib_metadata import entry_points
else:
    from importlib.metadata import entry_points


# The committed snapshots are generated on the lowest supported Python. On 3.12+,
# SQLite grows a transaction button, so the tests that show one render differently
# and skip their snapshot assertions (see the transaction_button_visible fixture).
SNAPSHOT_PYTHON = (3, 10)

LEGACY_CACHE_FILE = "catalog-cache-2.pickle"
LEGACY_HISTORY: dict[str, list[tuple[str, datetime, int, float]]] = {
    "abc123": [
        ("select 1", datetime(2026, 8, 1, 9, 30), 1, 0.5),
        ("sel", datetime(2026, 8, 1, 9, 31), -1, 0.0),
    ],
    "foo": [("select * from line_items", datetime(2026, 8, 1, 10, 30), 3, 1.25)],
}
"""What the committed version-2 cache holds, per connection: the four fields a
record of that version had -- sql, when it ran in local time, how many rows it
returned (negative for a failure), and how long it took in seconds."""


def pytest_configure(config: pytest.Config) -> None:
    """Stop --snapshot-update on a newer Python from clobbering the baseline.

    A full update run on 3.12+ silently rewrites the committed snapshots with
    3.12 output, and deletes the ones those tests never take. The py12-only
    snapshots can't be generated on 3.10 (their tests are skipped there), so
    updating those on 3.12 is allowed -- but such a run covers a slice of the
    suite, so it must not prune everything it didn't take.
    """
    if not config.option.update_snapshots:
        return
    if sys.version_info[:2] == SNAPSHOT_PYTHON:
        return

    baseline = ".".join(str(v) for v in SNAPSHOT_PYTHON)
    if "py12" not in (config.option.markexpr or ""):
        raise pytest.UsageError(
            f"--snapshot-update must run on Python {baseline}, which is what the "
            "committed snapshots were generated on:\n"
            "    uv run pytest --snapshot-update\n"
            "To update the py12-only snapshots, which can only be generated on "
            "3.12+, select just those tests:\n"
            "    uv run --python 3.12 --group test pytest -m 'py12 and not online' "
            "--snapshot-update"
        )
    config.option.no_cleanup = True


@pytest.fixture(scope="session", autouse=True)
def install_tzdata() -> None:
    if sys.platform == "win32" and not find_tzdata():
        download_tzdata()


@pytest.fixture(scope="session", autouse=True)
def set_locale_to_enUS() -> None:
    set_locale("en_US.UTF-8")


@pytest.fixture(autouse=True)
def crash_reports_go_to_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Every crash a test causes writes here, not into the developer's log dir.

    `run_test` re-raises through `_handle_exception`, so without this every
    failing functional test in the suite leaves a crash report behind.
    """
    report_dir = tmp_path / "crash-reports"
    monkeypatch.setattr("harlequin.crash.get_crash_report_dir", lambda: report_dir)
    return report_dir


@pytest.fixture(autouse=True)
def no_discovered_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the machine running the tests out of them.

    Config discovery walks the home directory, the user config dir and the cwd,
    so without this a developer's own `.harlequin.toml` decides what a test
    asserts.

    The search is the seam, rather than `load_config()` or a command's own
    `load_profile()`, because it is the one both commands share *and* that
    leaves `--config-path` working -- a test that passes an explicit config
    file still gets it. A test about discovery itself patches the seam back
    (see `test_discover_config_files`) or replaces it with directories of its
    own (see the `config_dirs` fixture).
    """
    monkeypatch.setattr("harlequin.config._search_directories", list)


@pytest.fixture(autouse=True)
def query_log_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the query log at a throwaway store so a test run never writes the
    developer's own history, and return where it went."""
    store = tmp_path / "history.db"
    monkeypatch.setattr("harlequin.query_log.default_path", lambda: store)
    return store


@pytest.fixture(autouse=True)
def catalog_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the catalog cache -- and the pickled history a migration reads --
    at a throwaway directory, and return it."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(
        "harlequin.catalog_cache.user_cache_dir", lambda **_: str(cache_dir)
    )
    return cache_dir


@pytest.fixture(autouse=True)
def forget_migrated_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test as a new process does, having adopted no connection."""
    monkeypatch.setattr("harlequin.history._migrated", set())


@pytest.fixture
def legacy_history_cache(catalog_cache_dir: Path, data_dir: Path) -> Path:
    """Put a version-2 catalog cache where Harlequin would find one.

    Written by Harlequin 2.14.0 (`scripts/write_legacy_cache.py`), so it names
    the classes it pickles exactly as a user's own file does. It holds
    `LEGACY_HISTORY`: two connections, one of whose queries failed.
    """
    catalog_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = catalog_cache_dir / f"catalog-cache-{HISTORY_CACHE_VERSION}.pickle"
    cache_file.write_bytes((data_dir / LEGACY_CACHE_FILE).read_bytes())
    return cache_file


@pytest.fixture
def data_dir() -> Path:
    here = Path(__file__)
    return here.parent / "data"


@pytest.fixture
def fake_ssh_client(data_dir: Path) -> Path:
    """The stand-in for the `ssh` binary, which needs no server to bind a forward."""
    return data_dir / "unit_tests" / "ssh" / "ssh"


@pytest.fixture
def drop_trigger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Touch the path this returns, and the fake client drops its forwards."""
    path = tmp_path / "drop"
    monkeypatch.setenv("FAKE_SSH_DROP_WHEN", str(path))
    return path


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
