from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

from harlequin.adapter import HarlequinAdapter
from harlequin.query import ResultSet, RowLimit, execute, fetch
from harlequin.statements import split


@pytest.fixture
def result_set(
    duckdb_adapter: type[HarlequinAdapter],
) -> Callable[..., ResultSet]:
    """Run one statement against an in-memory DuckDB and fetch its result.

    DuckDB rather than a fake cursor: everything downstream of `fetch()` is
    about how real values -- decimals, blobs, structs -- come out, and a fake
    would only prove that the fixture and the assertion agree.
    """
    connection = duckdb_adapter([":memory:"], no_init=True).connect()

    def _result_set(sql: str, limit: RowLimit | None = None) -> ResultSet:
        (executed,) = execute(connection, split(sql), limit=limit)
        return fetch(executed, limit=limit)

    return _result_set


@pytest.fixture
def run_python(tmp_path: Path) -> Callable[[str], subprocess.CompletedProcess[str]]:
    """Run a snippet in a fresh interpreter and capture its streams.

    In-process assertions can't see the state these tests care about: which
    modules an import pulled in, and which stream something was written to.
    Both are properties of a clean interpreter, and pytest has already imported
    half the world by the time a test runs.

    A clean *machine*, too: `no_discovered_config` cannot reach into a
    subprocess, so the child gets an empty directory as its cwd, its home and
    its config dir. Otherwise it reads the config files of whoever is running
    the tests.
    """
    env = {
        **os.environ,
        # where config discovery looks, on every platform platformdirs knows
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "APPDATA": str(tmp_path / "appdata"),
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
    }

    def _run(code: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            cwd=tmp_path,
            env=env,
        )

    return _run
