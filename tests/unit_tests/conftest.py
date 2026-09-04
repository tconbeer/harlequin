from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence

import pytest

from harlequin.adapter import HarlequinAdapter
from harlequin.query import ResultSet, RowLimit, execute, fetch
from harlequin.statements import split
from tests.hsql_sessions import HsqlSubprocess, ServeSession, WarmSession


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
    the tests -- or, with `HSQL_SESSION` set, runs every `main()` through the
    warm-session client and prefixes its stderr with a fallback warning.
    """
    env = {
        **{key: value for key, value in os.environ.items() if key != "HSQL_SESSION"},
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


@pytest.fixture
def hsql_subprocess(tmp_path: Path) -> HsqlSubprocess:
    """Run `main()` the way the console script does, in a fresh interpreter.

    Bytes rather than text, because the bytes are what hsql promises. A clean
    machine, as `run_python` gives: the child's cwd, home and config dir are
    empty directories unless a test names a cwd, and no `HSQL_SESSION` of
    whoever runs the tests reaches it -- a test that wants a session names
    one in `env`.
    """

    def _run(
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\n"
                f"sys.argv = ['hsql', *{list(argv)!r}]\n"
                "from harlequin.hsql import main\n"
                "main()\n",
            ],
            capture_output=True,
            input=stdin,
            cwd=tmp_path if cwd is None else cwd,
            env={
                **{
                    key: value
                    for key, value in os.environ.items()
                    if key not in ("HSQL_SESSION", "NO_COLOR")
                },
                "HOME": str(tmp_path),
                "USERPROFILE": str(tmp_path),
                "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
                "APPDATA": str(tmp_path / "appdata"),
                "LOCALAPPDATA": str(tmp_path / "localappdata"),
                **(env or {}),
            },
        )

    return _run


@pytest.fixture
def short_runtime_dir() -> Iterator[Path]:
    """A runtime directory a socket path still fits under.

    `sun_path` holds 104 bytes on macOS and pytest's `tmp_path` is longer than
    that there before a socket name is added.
    """
    base = tempfile.mkdtemp(prefix="hsql-", dir="/tmp")
    yield Path(base)
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def serve_session(short_runtime_dir: Path, tmp_path: Path) -> Iterator[ServeSession]:
    """Start sessions, and stop every one of them when the test is done."""
    started: list[WarmSession] = []

    def _serve(
        name: str = "test",
        *serve_argv: str,
        wait: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> WarmSession:
        session = WarmSession(
            name,
            serve_argv or ("-a", "duckdb", "--no-init", ":memory:"),
            runtime_dir=short_runtime_dir,
            home=tmp_path,
            env=env,
        )
        started.append(session)
        if wait:
            session.wait_until_ready()
        return session

    yield _serve
    for session in started:
        session.stop()
