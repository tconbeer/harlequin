"""The half of a warm session that holds the connection: `hsql --serve`.

What is asserted here is the server's half of the contract: which options each
role takes, that requests run one at a time on one connection, that the
session's state is what `--session-reset` clears, and the ways a session comes
down. Byte-equivalence with the cold path is `test_hsql_equivalence.py`'s.

Servers run in a fresh interpreter, because a server swaps the process's
streams, environment and working directory for every request; the command's
own refusals run in process, through the same `Served` the server hands it.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import click
import pytest
from click.testing import CliRunner, Result

from harlequin.exception import HarlequinConnectionError
from harlequin.hsql import protocol, server
from harlequin.hsql.cli import (
    CONFIG_OPTIONS,
    CONNECTION_OPTIONS,
    PER_REQUEST_OPTIONS,
    ROLE_OPTIONS,
    SERVER_OPTIONS,
    bare_command,
    build_cli,
)
from harlequin.hsql.diagnostics import ExitCode
from harlequin.hsql.server import Served, Server
from harlequin.hsql.timeout import Deadline, TimedOut
from tests.hsql_sessions import HsqlSubprocess, ServeSession, WarmSession

needs_unix_sockets = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"), reason="hsql sessions are POSIX-only"
)

Hsql = Callable[..., Result]


@pytest.fixture
def hsql(no_discovered_config: None) -> Hsql:
    """Invoke hsql in process, cold, or served when `obj` names a session."""
    runner = CliRunner()

    def _run(*args: str, **kwargs: Any) -> Result:
        argv = [str(arg) for arg in args]
        return runner.invoke(build_cli(argv), argv, catch_exceptions=False, **kwargs)

    return _run


@pytest.fixture
def duck() -> list[str]:
    return ["-a", "duckdb", "--no-init", ":memory:"]


@pytest.fixture
def in_process_server(duckdb_adapter: Any) -> Server:
    """A session with a real DuckDB connection, and no socket."""
    adapter = duckdb_adapter([":memory:"], no_init=True)
    return Server(
        "inproc",
        adapter="duckdb",
        connection=adapter.connect(),
        reconnect=adapter.connect,
    )


def served_by(server: Server, *, stdin: bytes | None = None) -> Served:
    return Served(
        server,
        protocol.Request(argv=[], cwd=os.getcwd(), environ={}, stdin=stdin),
    )


# --- the partition -----------------------------------------------------------


def test_every_option_is_in_exactly_one_group() -> None:
    """The rule that defines the two roles: `--serve` refuses the per-request
    group and a served request refuses the server's, so an option in neither
    or in both would be one nobody refuses -- or one both do."""
    declared = {param.name for param in bare_command().params if param.name}
    groups = [
        CONNECTION_OPTIONS,
        CONFIG_OPTIONS,
        PER_REQUEST_OPTIONS,
        SERVER_OPTIONS,
        ROLE_OPTIONS,
    ]
    assert declared == set().union(*groups)
    for first in groups:
        for second in groups:
            if first is not second:
                assert not first & second


def test_an_adapters_options_are_connection_options(hsql: Hsql) -> None:
    """The command declares none of them, so they are the group by default."""
    res = hsql("--serve", "prod", "-a", "duckdb", "--no-init", "--csv")
    assert res.exit_code == ExitCode.USAGE
    # `--csv` is refused, and `--no-init` was not -- it came first on the line
    assert "--csv is a per-request option" in res.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["-c", "select 1"],
        ["-f", "script.sql"],
        ["--format", "csv"],
        ["--limit", "10"],
        ["--timeout", "5"],
        ["--catalog"],
        ["--info"],
        ["-o", "out.csv"],
        ["--stats"],
    ],
)
def test_serve_refuses_per_request_options(hsql: Hsql, args: list[str]) -> None:
    """The feature's most likely first mistake, so the message names the right
    invocation rather than only the wrong one."""
    res = hsql("--serve", "prod", *args)
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "is a per-request option, and --serve takes none" in res.stderr
    assert "hsql --serve prod" in res.stderr
    assert "hsql --session prod" in res.stderr


def test_serve_and_session_are_two_roles(hsql: Hsql) -> None:
    res = hsql("--serve", "a", "--session", "b")
    assert res.exit_code == ExitCode.USAGE
    assert "--serve starts a session and --session sends to one" in res.stderr


def test_serve_does_not_reset(hsql: Hsql) -> None:
    """--session-reset is a client operation -- it asks a running session --
    so on --serve it is refused as the per-request option it is."""
    res = hsql("--serve", "a", "--session-reset")
    assert res.exit_code == ExitCode.USAGE
    assert "is a per-request option, and --serve takes none" in res.stderr


@pytest.mark.parametrize(
    "name,reason",
    [
        # a flag with no value is the caller's typo on every platform, so it is
        # answered before the platform is; the rest reach the name check only
        # where a session could have run
        ("", "needs a name"),
        pytest.param("bad name", "is not a session name", marks=needs_unix_sockets),
        pytest.param("../etc", "is not a session name", marks=needs_unix_sockets),
        pytest.param("x" * 65, "is not a session name", marks=needs_unix_sockets),
    ],
)
def test_serve_refuses_a_name_no_client_could_reach(
    hsql: Hsql, name: str, reason: str
) -> None:
    """Asked with the client's own check, and before a connection is paid for."""
    res = hsql("--serve", name, "-a", "duckdb", ":memory:")
    assert res.exit_code == ExitCode.USAGE
    assert reason in res.stderr


def test_queue_timeout_belongs_to_serve(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "--queue-timeout", "2", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert "--queue-timeout is a --serve option" in res.stderr


def test_session_reset_needs_a_session(hsql: Hsql) -> None:
    """It reconnects a running session, so with none named there is nothing to
    do -- and it attaches no adapter options, so it takes no connection args."""
    res = hsql("--session-reset")
    assert res.exit_code == ExitCode.USAGE
    assert "names none" in res.stderr
    assert "HSQL_SESSION" in res.stderr


def test_a_session_flag_that_reached_the_parser_is_refused(
    hsql: Hsql, duck: list[str]
) -> None:
    """`main()` reads it before it parses anything, so only a caller that built
    the command some other way can put it here."""
    res = hsql(*duck, "--session", "prod", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert "--session prod is read before hsql parses anything else" in res.stderr


@pytest.mark.parametrize("key", ["session", "serve"])
def test_a_profile_may_not_say_which_process_runs_an_invocation(
    hsql: Hsql, tmp_path: Path, key: str
) -> None:
    """The `CLI_ONLY_SSH_KEYS`-shaped refusal: `session` is decided before any
    config file is read, and a profile that served would turn a query into a
    daemon."""
    path = tmp_path / "hsql.toml"
    path.write_text(f'[profiles.prod]\nadapter = "duckdb"\n{key} = "prod"\n')
    res = hsql("--config-path", path, "-P", "prod", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert f"{key} says which process runs an invocation" in res.stderr
    assert f"--{key}" in res.stderr


def test_a_profiles_session_key_is_not_in_the_schema() -> None:
    from harlequin.config_schema import build_schema

    profile = build_schema(bare_command().params, adapters=None)["$defs"]["profile"][
        "properties"
    ]
    assert "session" not in profile
    assert "serve" not in profile
    # while the keys a profile may set are
    assert "queue_timeout" in profile


def test_session_is_a_profile_key_the_ide_leaves_alone() -> None:
    """One profile serves both commands, and the IDE reads hsql's keys off the
    command rather than a copy, so the new ones are there without a change."""
    from harlequin.cli import hsql_profile_keys

    assert {"session", "serve", "session_reset", "queue_timeout"} <= hsql_profile_keys()


# --- a served request, in process --------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["-a", "sqlite"],
        ["--read-only"],
        ["--ssh-host", "bastion"],
        ["--no-init"],
        ["other.db"],
    ],
)
def test_a_served_request_may_not_type_a_connection_option(
    hsql: Hsql, in_process_server: Server, args: list[str]
) -> None:
    """The session connected when it started, so its connection is fixed: a
    request that typed a connection option would otherwise run on the session's
    connection while believing it had changed it."""
    res = hsql(*args, "-c", "select 1", obj=served_by(in_process_server))
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "is a connection option, and the session named 'inproc' connected" in (
        res.stderr
    )


def test_a_served_request_takes_a_profile_of_per_request_options(
    hsql: Hsql, in_process_server: Server, tmp_path: Path
) -> None:
    """A profile names where options come from rather than being one, so what
    it holds decides: nothing here says which database, so it applies -- the
    same way a `default_profile` discovered in the caller's directory does."""
    path = tmp_path / "hsql.toml"
    path.write_text('[profiles.csv-out]\nformat = "csv"\nlimit = 5\n')
    res = hsql(
        "--config-path",
        path,
        "-P",
        "csv-out",
        "-c",
        "select 1 as a",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "a\n1\n"


@pytest.mark.parametrize(
    "key,value",
    [("adapter", '"sqlite"'), ("read_only", "true"), ("conn_str", '["other.db"]')],
)
def test_a_served_request_refuses_a_profile_that_names_a_connection(
    hsql: Hsql, in_process_server: Server, tmp_path: Path, key: str, value: str
) -> None:
    """A typed profile that answers what the session answered at start-up is
    refused under the key that answers it, rather than as a flag."""
    path = tmp_path / "hsql.toml"
    path.write_text(f"[profiles.prod]\n{key} = {value}\n")
    res = hsql(
        "--config-path",
        path,
        "-P",
        "prod",
        "-c",
        "select 1",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert f"the profile 'prod' sets {key}" in res.stderr
    assert "--serve NAME -P prod" in res.stderr


def test_a_served_request_may_not_type_a_server_option(
    hsql: Hsql, in_process_server: Server
) -> None:
    res = hsql(
        "--queue-timeout", "3", "-c", "select 1", obj=served_by(in_process_server)
    )
    assert res.exit_code == ExitCode.USAGE
    assert "--queue-timeout is a --serve option, and the session named 'inproc'" in (
        res.stderr
    )


def test_a_served_request_may_not_serve(hsql: Hsql, in_process_server: Server) -> None:
    res = hsql("--serve", "other", obj=served_by(in_process_server))
    assert res.exit_code == ExitCode.USAGE
    assert "pass one of them" in res.stderr


def test_a_mode_that_reads_no_database_is_not_the_sessions_business(
    hsql: Hsql, in_process_server: Server
) -> None:
    """`--info -a sqlite` narrows a document; it does not ask to reconnect."""
    res = hsql("--info", "-a", "sqlite", obj=served_by(in_process_server))
    assert res.exit_code == ExitCode.OK
    assert '"sqlite"' in res.stdout


def test_a_served_request_runs_on_the_sessions_connection(
    hsql: Hsql, in_process_server: Server
) -> None:
    served = served_by(in_process_server)
    assert hsql("-c", "create temp table t as select 1 as v", obj=served).exit_code == 0
    res = hsql("-tAc", "select v from t", obj=served)
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "1\n"


def test_a_dash_f_dash_with_no_stdin_is_refused(
    hsql: Hsql, in_process_server: Server
) -> None:
    """The client's argv scan misses a `-f` behind a boolean short an adapter
    declares, and an empty script run as if it were what was piped is the
    silent failure this turns into an exit 2."""
    res = hsql("-f", "-", obj=served_by(in_process_server, stdin=None))
    assert res.exit_code == ExitCode.USAGE
    assert "carried none" in res.stderr
    assert "--file -" in res.stderr


def test_a_dash_f_dash_with_stdin_is_allowed(
    hsql: Hsql, in_process_server: Server
) -> None:
    """The guard refuses only a `-f -` whose request carried no stdin; with one
    it reads the caller's SQL. (The bytes come through the server's own stdin
    stand-in end to end; here the CliRunner supplies them.)"""
    served = served_by(in_process_server, stdin=b"select 5 as five")
    res = hsql("-tAf", "-", obj=served, input="select 5 as five")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "5\n"


def test_session_reset_reconnects(hsql: Hsql, in_process_server: Server) -> None:
    served = served_by(in_process_server)
    hsql("-c", "create temp table t as select 1 as v", obj=served)
    res = hsql("--session-reset", obj=served)
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert "reconnected" in res.stderr
    assert hsql("-c", "select v from t", obj=served).exit_code == ExitCode.QUERY


def test_a_reset_that_cannot_reconnect_leaves_the_session_without_a_connection(
    hsql: Hsql, duckdb_adapter: Any
) -> None:
    """Exit 3 for the reset, and exit 3 for every request after it until a
    reset succeeds: a session with no connection says so rather than pretending."""
    adapter = duckdb_adapter([":memory:"], no_init=True)
    attempts: list[int] = []

    def reconnect() -> Any:
        attempts.append(1)
        if len(attempts) == 1:
            raise HarlequinConnectionError("the warehouse is down")
        return adapter.connect()

    session = Server(
        "flaky", adapter="duckdb", connection=adapter.connect(), reconnect=reconnect
    )
    served = served_by(session)
    res = hsql("--session-reset", obj=served)
    assert res.exit_code == ExitCode.CONNECTION
    assert "the warehouse is down" in res.stderr
    res = hsql("-c", "select 1", obj=served)
    assert res.exit_code == ExitCode.CONNECTION
    assert "has no connection" in res.stderr
    assert "--session-reset" in res.stderr
    assert hsql("--session-reset", obj=served).exit_code == ExitCode.OK
    assert hsql("-c", "select 1", obj=served).exit_code == ExitCode.OK


# --- a cancel that does not land ---------------------------------------------


class _FakeConnection:
    def __init__(self, label: str) -> None:
        self.label = label
        self.closed = False

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_a_deadline_a_server_gave_an_abandon_hook_ends_the_run_not_the_process() -> (
    None
):
    """Where the cold path halts the process, a deadline given an abandon hook
    calls it and raises TimedOut for the request to attribute, so the server
    lives on."""
    abandoned: list[bool] = []
    never = threading.Event()
    connection: Any = _FakeConnection("held")
    deadline = Deadline(0.01, grace=0.01, abandon=lambda: abandoned.append(True))
    with pytest.raises(TimedOut):
        deadline.run(lambda: never.wait(30), connection=connection)
    assert abandoned == [True]
    never.set()


def test_an_abandoned_connection_is_offered_to_nobody_until_a_reset() -> None:
    """A cancel that outlasts its grace leaves the connection to the thread
    still inside it, so `abandon()` marks the session unusable until a reset,
    rather than handing the next request a connection another thread holds."""
    opened: list[Any] = [_FakeConnection("first")]

    def reconnect() -> Any:
        opened.append(_FakeConnection(f"reconnect-{len(opened)}"))
        return opened[-1]

    session = Server(
        "stuck", adapter="duckdb", connection=opened[0], reconnect=reconnect
    )
    assert session.connection() is opened[0]
    session.abandon()
    with pytest.raises(HarlequinConnectionError) as raised:
        session.connection()
    assert "cancelled a query that did not stop" in raised.value.msg
    assert "--session-reset" in raised.value.msg
    # the abandoned connection is left to its thread, not closed under it
    assert not opened[0].closed  # type: ignore[attr-defined]
    session.reset()
    assert session.connection() is opened[-1]


# --- the turnstile ---------------------------------------------------------------


def test_the_turnstile_serves_in_arrival_order() -> None:
    turnstile = server.Turnstile()
    order: list[int] = []
    assert turnstile.enter(None)

    def wait_turn(number: int) -> None:
        turnstile.enter(None)
        order.append(number)
        turnstile.leave()

    threads = []
    for number in (1, 2):
        thread = threading.Thread(target=wait_turn, args=(number,))
        thread.start()
        threads.append(thread)
        # each ticket is taken before the next thread starts, so the arrival
        # order is this loop's rather than the scheduler's
        _until(lambda waiting=number: turnstile.queued == waiting)  # type: ignore[misc]

    turnstile.leave()
    for thread in threads:
        thread.join(5)
    assert order == [1, 2]


def _until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    """Block until `condition` holds, so a test orders threads by observation."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("the condition never held")


def test_the_turnstile_gives_up_on_a_deadline() -> None:
    turnstile = server.Turnstile()
    assert turnstile.enter(None)
    started = time.monotonic()
    assert not turnstile.enter(0.05)
    assert time.monotonic() - started < 2
    assert turnstile.queued == 0
    turnstile.leave()
    assert turnstile.enter(0.05)


# --- the recorder -------------------------------------------------------------------


def test_the_recorder_keeps_the_order_the_streams_were_written_in() -> None:
    """A note between two result sets reaches the terminal between them."""
    recorder = server.Recorder(stdout_isatty=True)
    out, err = recorder.stdout(), recorder.stderr()
    out.write("one\n")
    out.buffer.write(b"two\n")
    err.write("note\n")
    out.write("three\n")
    assert [(kind, bytes(data)) for kind, data in recorder.segments] == [
        (protocol.STDOUT, b"one\ntwo\n"),
        (protocol.STDERR, b"note\n"),
        (protocol.STDOUT, b"three\n"),
    ]
    assert out.isatty() and not err.isatty()


def test_click_writes_binary_output_to_the_recorder() -> None:
    """`-o -` is `click.open_file("-", "wb")`, which has to find the buffer."""
    recorder = server.Recorder()
    stream = recorder.stdout()
    saved = sys.stdout
    sys.stdout = stream
    try:
        click.open_file("-", mode="wb").write(b"bytes\n")
        click.echo("text")
    finally:
        sys.stdout = saved
    assert bytes(recorder.segments[0][1]) == b"bytes\ntext\n"


# --- a real server, in a fresh interpreter -------------------------------------


@pytest.fixture
def warm(serve_session: ServeSession) -> WarmSession:
    return serve_session("warm")


@pytest.fixture
def send(hsql_subprocess: HsqlSubprocess, warm: WarmSession) -> HsqlSubprocess:
    """One invocation, sent to the session by the real console script."""

    def _send(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return hsql_subprocess(["--session", warm.name, *argv], env=warm.env, **kwargs)

    return _send


@needs_unix_sockets
def test_a_session_answers(send: HsqlSubprocess, warm: WarmSession) -> None:
    proc = send(["-c", "select 1 as a"])
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b" a\n---\n 1\n(1 row)\n"
    assert proc.stderr == b""


@needs_unix_sockets
@pytest.mark.session_divergent
def test_a_temp_table_survives_between_invocations(
    send: HsqlSubprocess, hsql_subprocess: HsqlSubprocess
) -> None:
    """The single most useful thing about the feature, and the one nobody reads
    the docs to discover. Cold, the second invocation has no such table."""
    assert (
        send(
            ["--format", "none", "-c", "create temp table t as select 7 as v"]
        ).returncode
        == 0
    )
    warm_result = send(["-tAc", "select v from t"])
    assert warm_result.returncode == ExitCode.OK
    assert warm_result.stdout == b"7\n"
    cold = ["-a", "duckdb", "--no-init", ":memory:"]
    assert (
        hsql_subprocess(
            [*cold, "-c", "create temp table t as select 7 as v"]
        ).returncode
        == 0
    )
    cold_result = hsql_subprocess([*cold, "-tAc", "select v from t"])
    assert cold_result.returncode == ExitCode.QUERY
    assert b"does not exist" in cold_result.stderr


@needs_unix_sockets
@pytest.mark.session_divergent
def test_a_setting_survives_between_invocations(send: HsqlSubprocess) -> None:
    assert send(["--format", "none", "-c", "set TimeZone='Asia/Tokyo'"]).returncode == 0
    proc = send(["-tAc", "select current_setting('TimeZone')"])
    assert proc.stdout == b"Asia/Tokyo\n"


@needs_unix_sockets
@pytest.mark.session_divergent
def test_a_reset_clears_the_sessions_state(send: HsqlSubprocess) -> None:
    send(["--format", "none", "-c", "create temp table t as select 7 as v"])
    proc = send(["--session-reset"])
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b""
    assert b"reconnected" in proc.stderr
    assert send(["-c", "select v from t"]).returncode == ExitCode.QUERY


@needs_unix_sockets
def test_a_relative_output_path_is_the_clients(
    send: HsqlSubprocess, tmp_path: Path
) -> None:
    """The server writes `-o`, resolved where the caller is, not where it is."""
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    proc = send(["--csv", "-o", "out.csv", "-c", "select 1 as a"], cwd=client_dir)
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b""
    assert (client_dir / "out.csv").read_bytes() == b"a\n1\n"


@needs_unix_sockets
def test_a_relative_script_path_is_the_clients(
    send: HsqlSubprocess, tmp_path: Path
) -> None:
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    (client_dir / "q.sql").write_text("select 'from a file' as a")
    proc = send(["-tAf", "q.sql"], cwd=client_dir)
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b"from a file\n"


@needs_unix_sockets
def test_a_config_file_where_the_client_is_applies_its_per_request_keys(
    send: HsqlSubprocess, tmp_path: Path
) -> None:
    """Config discovery is cwd-dependent, and the cwd is the client's."""
    client_dir = tmp_path / "project"
    client_dir.mkdir()
    (client_dir / ".harlequin.toml").write_text(
        'default_profile = "here"\n[profiles.here]\nformat = "csv"\n'
    )
    proc = send(["-c", "select 1 as a"], cwd=client_dir)
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b"a\n1\n"


@needs_unix_sockets
def test_piped_sql_reaches_the_session(send: HsqlSubprocess) -> None:
    proc = send(["-f", "-", "--csv"], stdin=b"select 3 as three")
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b"three\n3\n"


@needs_unix_sockets
def test_the_servers_own_streams_carry_only_its_log(
    warm: WarmSession, send: HsqlSubprocess
) -> None:
    """Result sets go to the client that asked; the operator sees a line per request."""
    send(["-c", "select 'to the client' as a"])
    send(["-c", "select * from nowhere"])
    assert warm.stop() == ExitCode.OK
    log = warm.stderr()
    assert "to the client" not in log
    assert "session 'warm' is ready (duckdb)" in log
    assert "request 1: exit 0" in log
    assert "request 2: exit 1" in log
    assert "session 'warm' stopped after 2 requests" in log
    assert warm.process.stdout is not None


@needs_unix_sockets
def test_a_stopped_session_takes_its_socket_with_it(warm: WarmSession) -> None:
    assert warm.socket_path.exists()
    assert warm.stop() == ExitCode.OK
    assert not warm.socket_path.exists()


@needs_unix_sockets
def test_a_second_server_under_the_same_name_is_refused(
    warm: WarmSession, serve_session: ServeSession
) -> None:
    second = serve_session("warm", wait=False)
    assert second.process.wait(30) == ExitCode.USAGE
    assert "already running" in second.stderr()
    # and the first is untouched: its socket is still the one that answers
    assert warm.socket_path.exists()


@needs_unix_sockets
def test_a_stale_socket_is_replaced(
    short_runtime_dir: Path, serve_session: ServeSession
) -> None:
    """A server that died leaves its file; the next one under that name is
    what unlinks it, since a client never does."""
    from harlequin.hsql.session import socket_path

    (short_runtime_dir / "hsql").mkdir(mode=0o700)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(socket_path("warm", {"XDG_RUNTIME_DIR": str(short_runtime_dir)}))
    stale.close()
    session = serve_session("warm")
    assert session.socket_path.exists()
    assert session.stop() == ExitCode.OK


@needs_unix_sockets
def test_a_runtime_dir_someone_else_can_reach_is_refused(
    short_runtime_dir: Path, serve_session: ServeSession
) -> None:
    shared = short_runtime_dir / "hsql"
    shared.mkdir()
    os.chmod(shared, 0o777)
    session = serve_session("warm", wait=False)
    assert session.process.wait(30) == ExitCode.USAGE
    assert "cannot listen" in session.stderr()


@needs_unix_sockets
def test_serve_on_a_connection_that_fails_exits_3(serve_session: ServeSession) -> None:
    session = serve_session(
        "warm", "-a", "duckdb", "--no-init", "/nonexistent/dir/x.db", wait=False
    )
    assert session.process.wait(30) == ExitCode.CONNECTION


class Blocked:
    """A request the test holds open: the server is reading a FIFO for its SQL.

    Deterministic where a slow query is not: opening the FIFO for writing
    blocks until the server has opened it to read, which is after the request
    took its turn, so everything sent after that is queued behind it.
    """

    def __init__(self, fifo: Path, warm: WarmSession, tmp_path: Path) -> None:
        self.fifo = fifo
        os.mkfifo(fifo)
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys\n"
                f"sys.argv = ['hsql', '--session', {warm.name!r}, "
                f"'--format', 'none', '-f', {str(fifo)!r}]\n"
                "from harlequin.hsql import main\n"
                "main()\n",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmp_path,
            env={**os.environ, **warm.env, "HOME": str(tmp_path)},
        )
        self.writer = open(fifo, "w")

    def release(self, sql: str = "select 1") -> None:
        self.writer.write(sql)
        self.writer.close()


@pytest.fixture
def blocked(
    warm: WarmSession, short_runtime_dir: Path, tmp_path: Path
) -> Iterator[Blocked]:
    held = Blocked(short_runtime_dir / "script.fifo", warm, tmp_path)
    yield held
    if held.process.poll() is None:
        held.process.kill()
    held.process.wait(5)
    if held.process.stdout is not None:
        held.process.stdout.close()
    if held.process.stderr is not None:
        held.process.stderr.close()


@needs_unix_sockets
@pytest.mark.session_divergent
def test_a_second_client_waits_its_turn(blocked: Blocked, send: HsqlSubprocess) -> None:
    """One connection, one request at a time: the second client's query sees
    what the first one, still running when it was sent, went on to create."""
    second = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "sys.argv = ['hsql', '--session', 'warm', '-tAc', 'select v from q']\n"
            "from harlequin.hsql import main\n"
            "main()\n",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, **blocked_env(blocked)},
    )
    blocked.release("create temp table q as select 42 as v")
    out, err = second.communicate(timeout=30)
    assert second.returncode == ExitCode.OK, err
    assert out == b"42\n"
    assert blocked.process.wait(30) == ExitCode.OK


def blocked_env(blocked: Blocked) -> dict[str, str]:
    return {"XDG_RUNTIME_DIR": str(blocked.fifo.parent)}


@needs_unix_sockets
def test_a_client_that_dies_mid_query_does_not_take_the_session_down(
    blocked: Blocked, send: HsqlSubprocess, warm: WarmSession
) -> None:
    blocked.process.kill()
    blocked.process.wait(5)
    blocked.release("select 1")
    proc = send(["-tAc", "select 'still up'"])
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b"still up\n"
    assert warm.stop() == ExitCode.OK
    assert "a client went away" in warm.stderr()


@needs_unix_sockets
def test_a_timeout_stops_the_request_and_leaves_the_session_up(
    send: HsqlSubprocess,
) -> None:
    """The whole chain a served `--timeout` crosses: the deadline cancels, the
    request attributes it and exits 4, and the session is usable straight
    after -- DuckDB's cancel lands inside the grace period, so the connection
    is never abandoned. (The grace running out is `Deadline`'s own test: no
    adapter that cancels can reach it.)"""
    proc = send(["--timeout", "0.1", "-c", "select count(*) from range(2000000000)"])
    assert proc.returncode == ExitCode.TIMEOUT
    assert proc.stdout == b""
    assert b"timed out after 0.1s" in proc.stderr
    assert send(["-tAc", "select 'still up'"]).stdout == b"still up\n"


@needs_unix_sockets
def test_a_queue_timeout_is_not_a_query_timeout(
    serve_session: ServeSession,
    hsql_subprocess: HsqlSubprocess,
    short_runtime_dir: Path,
    tmp_path: Path,
) -> None:
    """A client that never reached the database exits 4 saying so -- a
    different fact from a query that ran too long, and one the caller acts on
    differently."""
    session = serve_session(
        "queued", "--queue-timeout", "0.2", "-a", "duckdb", "--no-init", ":memory:"
    )
    held = Blocked(short_runtime_dir / "q.fifo", session, tmp_path)
    try:
        proc = hsql_subprocess(
            ["--session", "queued", "-c", "select 1"], env=session.env
        )
        assert proc.returncode == ExitCode.TIMEOUT
        assert proc.stdout == b""
        assert b"never reached the database" in proc.stderr
        assert b"--queue-timeout" in proc.stderr
    finally:
        held.release()
        held.process.wait(30)
    assert (
        hsql_subprocess(
            ["--session", "queued", "-tAc", "select 2"], env=session.env
        ).stdout
        == b"2\n"
    )


# --- the wire, from a client that is not ours -------------------------------------


def raw_request(
    warm: WarmSession, **fields: Any
) -> tuple[list[tuple[int, bytes]], int]:
    """Send one request over the protocol by hand, and return what came back."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with connection:
        connection.connect(str(warm.socket_path))
        hello = protocol.recv_frame(connection)
        assert hello == (protocol.HELLO, protocol.VERSION.encode())
        request = {
            "argv": [],
            "cwd": os.getcwd(),
            "environ": {},
            "stdin": None,
            **fields,
        }
        protocol.send_frame(
            connection, protocol.REQUEST, protocol.pack_request(**request)
        )
        frames: list[tuple[int, bytes]] = []
        while True:
            frame = protocol.recv_frame(connection)
            assert frame is not None
            if frame[0] == protocol.EXIT:
                return frames, int.from_bytes(frame[1], "big")
            frames.append(frame)


@needs_unix_sockets
def test_the_server_refuses_a_dash_f_dash_that_carried_no_stdin(
    warm: WarmSession,
) -> None:
    """The check that turns every miss of the client's argv scan into an exit 2."""
    frames, code = raw_request(warm, argv=["-f", "-"], stdin=None)
    assert code == ExitCode.USAGE
    assert b"carried none" in b"".join(
        data for kind, data in frames if kind == protocol.STDERR
    )


@needs_unix_sockets
def test_the_server_refuses_a_working_directory_it_cannot_enter(
    warm: WarmSession,
) -> None:
    frames, code = raw_request(
        warm, argv=["-c", "select 1"], cwd="/nonexistent/anywhere"
    )
    assert code == ExitCode.USAGE
    assert b"cannot be entered" in b"".join(
        data for kind, data in frames if kind == protocol.STDERR
    )


@needs_unix_sockets
def test_the_servers_stdout_is_chunked(warm: WarmSession) -> None:
    """Many `STDOUT` frames then one `EXIT`, so that streaming has somewhere to land."""
    wide = "select repeat('x', 1000) as a from range(200)"
    frames, code = raw_request(warm, argv=["-tA", "--display-rows", "-1", "-c", wide])
    assert code == ExitCode.OK
    stdout = [data for kind, data in frames if kind == protocol.STDOUT]
    assert len(stdout) > 1
    assert all(len(chunk) <= protocol.CHUNK_SIZE for chunk in stdout)
    assert b"".join(stdout) == (b"x" * 1000 + b"\n") * 200


@needs_unix_sockets
def test_a_client_that_sends_no_request_is_dropped(
    warm: WarmSession, send: HsqlSubprocess
) -> None:
    """A version it refused to be served by, or one that died: the server
    reads the hello, hears nothing back, and goes on serving."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with connection:
        connection.connect(str(warm.socket_path))
        assert protocol.recv_frame(connection) is not None
    assert send(["-tAc", "select 1"]).stdout == b"1\n"


@needs_unix_sockets
def test_color_auto_reads_the_callers_terminal(warm: WarmSession) -> None:
    """On the server every stream is a buffer, so the request has to say."""
    argv = ["--color", "auto", "-c", "select 1 as a"]
    plain, _ = raw_request(warm, argv=argv, stdout_isatty=False)
    styled, _ = raw_request(warm, argv=argv, stdout_isatty=True)
    assert b"\x1b[" not in b"".join(data for _, data in plain)
    assert b"\x1b[" in b"".join(data for _, data in styled)
    honored, _ = raw_request(
        warm, argv=argv, stdout_isatty=True, environ={"NO_COLOR": "1"}
    )
    assert b"\x1b[" not in b"".join(data for _, data in honored)


@needs_unix_sockets
def test_the_peer_uid_is_ours_on_a_socketpair() -> None:
    left, right = socket.socketpair(socket.AF_UNIX)
    with left, right:
        uid = server.peer_uid(right)
    assert uid is None or uid == os.getuid()
