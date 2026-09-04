"""One invocation produces the same bytes cold and warm.

The risk a session carries is not that it fails but that it drifts: that in
some corner -- a NULL, a `-t` footer, an error's exit code, a truncation notice
-- the served path and the cold path produce different bytes, and a caller who
set `HSQL_SESSION` months ago gets an answer the docs do not describe.

The claim is about one invocation, from a fresh session. Given the same
starting state, an invocation writes the same stdout, the same stderr and exits
the same whether it ran cold or warm; nothing is claimed about what the *next*
invocation sees, which is the feature (`@pytest.mark.session_divergent` is
where those live). So every case here runs twice, both through the real
console script, and the session is reset between cases.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import Iterator, Sequence

import pytest

from harlequin.hsql.diagnostics import ExitCode
from tests.hsql_sessions import HsqlSubprocess, ServeSession, WarmSession

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"), reason="hsql sessions are POSIX-only"
)

COLD = ["-a", "duckdb", "--no-init", ":memory:"]
"""What the session connects with, so a cold run connects the same way."""

TEN_ROWS = (
    "with recursive t(n) as ("
    "select 1 union all select n + 1 from t where n < 10"
    ") select n from t"
)

ELAPSED = re.compile(rb'"elapsed_ms":\d+')
"""The one field of `--stats` that two runs of anything never agree on."""

# Cases that connect and run SQL (or error at/after the connect step): cold
# carries the connection, warm carries the session, and the per-request args
# are the same. This is the drift surface the design cares about -- a NULL, a
# footer, a truncation notice, an error's exit code.
CONNECTING: dict[str, list[str]] = {
    "select": ["-c", "select 1 as a, 'two' as b, null as c"],
    "tuples-only": ["-tAc", "select 42"],
    "psql-algebra": ["--no-header", "--no-footer", "-c", "select 1 as a"],
    "null-string": ["--null-string", "∅", "-c", "select null as a"],
    "csv": ["--csv", "-c", "select 1 as a, null as b"],
    "csv-null": ["--csv", "--null-string", "NULL", "-c", "select null as a"],
    "json": ["--json", "-c", "select 1 as a, 'x' as b"],
    "jsonl": ["--jsonl", "-c", "select 1 as a; select 2 as a"],
    "markdown": ["--markdown", "-c", "select 1 as a"],
    "vertical": ["-x", "-c", "select 1 as a, 2 as b"],
    "none": ["--format", "none", "-c", "select 1"],
    "unicode": ["-c", "select '中文' as a, 'ab' as b"],
    "types": [
        "-c",
        "select 12345.6789::decimal(18, 4) as d, date '2024-03-01' as day, "
        "[1, 2] as items, {'a': 1} as record, '\\x00\\xFF'::blob as payload",
    ],
    "truncated": ["--limit", "3", "-c", TEN_ROWS],
    "row-cap": ["--display-rows", "2", "-tc", TEN_ROWS],
    "row-cap-ignored": ["--csv", "--display-rows", "2", "-c", TEN_ROWS],
    "two-results": ["-c", "select 1 as a", "-c", "select 2 as b"],
    "result-last": ["--result", "last", "-c", "select 1 as a; select 2 as b"],
    "result-index": ["--result", "2", "-c", "select 1 as a; select 2 as b"],
    "result-out-of-range": ["--result", "9", "-c", "select 1"],
    "one-csv-two-results": ["--csv", "-c", "select 1; select 2"],
    "on-error-stop": ["-c", "select * from nowhere; select 1 as after"],
    "on-error-continue": [
        "--on-error",
        "continue",
        "-c",
        "select * from nowhere; select 1 as after",
    ],
    "query-error": ["-c", "select from"],
    "empty-result": ["--csv", "-c", "select 1 as a where false"],
    "stats": ["--stats", "--limit", "3", "-c", TEN_ROWS],
    "stats-error": ["--stats", "-c", "select * from nowhere"],
    "color-always": ["--color", "always", "-c", "select 1 as a"],
    "color-never": ["--color", "never", "-c", "select 1 as a"],
    "two-formats": ["--csv", "--json", "-c", "select 1"],
    "path-without-catalog": ["--path", "x", "-c", "select 1"],
    "catalog": ["--catalog"],
    "catalog-path": ["--catalog", "--path", "memory.main"],
    "catalog-search": ["--catalog-search", "zzz"],
    "catalog-limit-ignored": ["--catalog", "--limit", "5"],
    "file": ["-tAf", "q.sql"],
    "file-missing": ["-f", "missing.sql"],
    "stdin": ["--csv", "-f", "-"],
    "output-file": ["--csv", "-o", "out.csv", "-c", "select 1 as a"],
    "output-dir": ["--csv", "-o", "results/", "-c", "select 1 as a; select 2 as b"],
    "output-parquet": [
        "--format",
        "parquet",
        "-o",
        "out.parquet",
        "-c",
        "select 1 as a",
    ],
}

# Cases that never reach the connection: the modes, and the usage errors caught
# before connecting. The session runs them through the same command the cold
# path builds, so cold and warm run the identical argv -- warm just names the
# session -- and the equivalence is that the session did nothing to the bytes.
LOCAL: dict[str, list[str]] = {
    "bare": [],
    "bad-flag": ["--nope"],
    "two-modes": ["--catalog", "--spec"],
    "help": ["--help"],
    "version": ["--version"],
    "spec": ["--spec", "-a", "duckdb"],
    "info": ["--info", "-a", "duckdb"],
    "config-show": ["--config", "show"],
    "config-list-profiles": ["--config", "list-profiles"],
    "skill": ["--skill"],
}

STDIN = b"select 'piped' as a"


@pytest.fixture(scope="module")
def session(serve_session_module: ServeSession) -> WarmSession:
    return serve_session_module("eq")


@pytest.fixture(scope="module")
def serve_session_module(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ServeSession]:
    """One server per module per xdist worker -- and, below, a fresh session
    per case, which is what makes a shared server safe."""
    import shutil
    import tempfile

    runtime_dir = Path(tempfile.mkdtemp(prefix="hsql-", dir="/tmp"))
    home = tmp_path_factory.mktemp("session-home")
    started: list[WarmSession] = []

    def _serve(name: str) -> WarmSession:
        started.append(WarmSession(name, COLD, runtime_dir=runtime_dir, home=home))
        started[-1].wait_until_ready()
        return started[-1]

    yield _serve
    for session in started:
        session.stop()
    shutil.rmtree(runtime_dir, ignore_errors=True)


@pytest.fixture
def fresh(session: WarmSession, hsql_subprocess: HsqlSubprocess) -> WarmSession:
    """Equivalence holds from a fresh session, so every case gets one."""
    proc = hsql_subprocess(
        ["--session", session.name, "--session-reset"], env=session.env
    )
    assert proc.returncode == ExitCode.OK, proc.stderr
    return session


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """What the cases that read or write files find where they run."""
    (tmp_path / "q.sql").write_text("select 'from a file' as a")
    return tmp_path


def _normalize(stream: bytes) -> bytes:
    return ELAPSED.sub(b'"elapsed_ms":N', stream)


def _both(
    hsql_subprocess: HsqlSubprocess,
    workspace: Path,
    fresh: WarmSession,
    cold_argv: Sequence[str],
    warm_argv: Sequence[str],
    *,
    stdin: bytes | None,
) -> None:
    cold_dir, warm_dir = workspace / "cold", workspace / "warm"
    for directory in (cold_dir, warm_dir):
        directory.mkdir()
        (directory / "q.sql").write_bytes((workspace / "q.sql").read_bytes())

    cold = hsql_subprocess(cold_argv, cwd=cold_dir, stdin=stdin)
    warm = hsql_subprocess(
        ["--session", fresh.name, *warm_argv], cwd=warm_dir, stdin=stdin, env=fresh.env
    )

    assert warm.returncode == cold.returncode
    assert warm.stdout == cold.stdout
    assert _normalize(warm.stderr) == _normalize(cold.stderr)
    cold_files = {
        p.relative_to(cold_dir): p.read_bytes()
        for p in cold_dir.rglob("*")
        if p.is_file()
    }
    warm_files = {
        p.relative_to(warm_dir): p.read_bytes()
        for p in warm_dir.rglob("*")
        if p.is_file()
    }
    assert warm_files == cold_files


@pytest.mark.parametrize("argv", list(CONNECTING.values()), ids=list(CONNECTING))
def test_a_query_is_the_same_bytes_cold_and_warm(
    argv: Sequence[str],
    fresh: WarmSession,
    hsql_subprocess: HsqlSubprocess,
    workspace: Path,
) -> None:
    """Cold connects for itself; warm runs on the session's connection. Same
    starting state, same bytes."""
    stdin = STDIN if "-" in argv else None
    _both(hsql_subprocess, workspace, fresh, [*COLD, *argv], argv, stdin=stdin)


@pytest.mark.parametrize("argv", list(LOCAL.values()), ids=list(LOCAL))
def test_a_mode_is_the_same_bytes_cold_and_warm(
    argv: Sequence[str],
    fresh: WarmSession,
    hsql_subprocess: HsqlSubprocess,
    workspace: Path,
) -> None:
    """A mode reads no database, so the session runs the identical argv the
    cold path would, and must not touch the bytes on the way."""
    _both(hsql_subprocess, workspace, fresh, argv, argv, stdin=None)


def test_the_cases_cover_every_exit_code_but_the_signals(
    fresh: WarmSession, hsql_subprocess: HsqlSubprocess, workspace: Path
) -> None:
    """A drift in an exit code is the one a script notices last."""
    codes = set()
    for argv in (CONNECTING["select"], CONNECTING["query-error"], LOCAL["bad-flag"]):
        codes.add(hsql_subprocess([*COLD, *argv], cwd=workspace).returncode)
    assert codes == {ExitCode.OK, ExitCode.QUERY, ExitCode.USAGE}
