"""The warm-session client, and the frames it speaks.

What is asserted here is the client's half of the contract: which invocations
belong to a session, what it does when none is running, and that a response it
is handed arrives on the caller's streams byte for byte. The stub server below
is built on `protocol` rather than on a copy of it, so the frames these tests
pin are the ones the real server sends.
"""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from importlib.metadata import version
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterator, Sequence

import pytest

from harlequin.hsql import client, protocol, session
from harlequin.hsql.diagnostics import ExitCode

HAS_UNIX_SOCKETS = hasattr(socket, "AF_UNIX")

needs_unix_sockets = pytest.mark.skipif(
    not HAS_UNIX_SOCKETS, reason="hsql sessions are POSIX-only"
)


# --- which invocations belong to a session -----------------------------------


@pytest.mark.parametrize(
    "argv,environ,expected",
    [
        ([], {}, None),
        (["-c", "select 1"], {}, None),
        (["-c", "select 1"], {"HSQL_SESSION": ""}, None),
        (["-c", "select 1"], {"HSQL_SESSION": "prod"}, ("prod", False)),
        (["--session", "prod", "-c", "x"], {}, ("prod", True)),
        (["--session=prod", "-c", "x"], {}, ("prod", True)),
        # a typed name beats an ambient one: it is the more specific of the two
        (["--session", "here"], {"HSQL_SESSION": "there"}, ("here", True)),
        # last wins, as it does for every option click parses
        (["--session", "first", "--session", "last"], {}, ("last", True)),
        (["--session=first", "--session=last"], {}, ("last", True)),
        # no value: the client refuses it by name rather than click, which
        # does not know the option yet
        (["--session"], {}, ("", True)),
        # after `--` everything is an argument, so this is a connection string
        (["--", "--session", "prod"], {}, None),
    ],
)
def test_the_session_an_invocation_names(
    argv: list[str],
    environ: dict[str, str],
    expected: tuple[str, bool] | None,
) -> None:
    named = session.requested_session(argv, environ)
    if expected is None:
        assert named is None
    else:
        assert named is not None
        assert (named.name, named.explicit) == expected


@pytest.mark.parametrize(
    "argv,expected",
    [
        ([], []),
        (["-c", "select 1"], ["-c", "select 1"]),
        (["--session", "prod", "-c", "x"], ["-c", "x"]),
        (["-c", "x", "--session=prod"], ["-c", "x"]),
        (["--session", "prod", "--", "db"], ["--", "db"]),
        (["--", "--session", "prod"], ["--", "--session", "prod"]),
    ],
)
def test_the_clients_own_flag_does_not_reach_the_server(
    argv: list[str], expected: list[str]
) -> None:
    """Everything else is forwarded opaquely, so that one parser sees it."""
    assert session.without_session_option(argv) == expected


@pytest.mark.parametrize(
    "name,valid",
    [
        ("prod", True),
        ("a", True),
        ("A_b-2", True),
        ("x" * session.MAX_NAME_LENGTH, True),
        ("", False),
        ("x" * (session.MAX_NAME_LENGTH + 1), False),
        ("../../etc/passwd", False),
        ("with space", False),
        ("dot.name", False),
    ],
)
def test_a_name_that_cannot_escape_the_directory(name: str, valid: bool) -> None:
    assert session.is_valid_name(name) is valid


@needs_unix_sockets
def test_the_socket_lives_under_the_runtime_dir() -> None:
    assert session.socket_path("prod", {"XDG_RUNTIME_DIR": "/run/user/7"}) == (
        "/run/user/7/hsql/prod.sock"
    )


@needs_unix_sockets
def test_the_fallback_runtime_dir_is_per_user() -> None:
    """The common path, not the exotic one: macOS, WSL2 without systemd, and a
    container all arrive here."""
    assert session.runtime_dir({"TMPDIR": "/tmp/x"}) == f"/tmp/x/hsql-{os.getuid()}"
    assert session.runtime_dir({}) == f"/tmp/hsql-{os.getuid()}"


@needs_unix_sockets
def test_a_directory_only_this_user_can_reach_is_accepted(tmp_path: Path) -> None:
    private = tmp_path / "run"
    private.mkdir(mode=0o700)
    session.check_runtime_dir(str(private))


@needs_unix_sockets
@pytest.mark.parametrize("mode", [0o777, 0o750, 0o701])
def test_a_directory_someone_else_can_reach_is_refused(
    mode: int, tmp_path: Path
) -> None:
    """What a client sends is argv, the cwd and piped stdin, and argv can carry
    a password. Under the TMPDIR fallback nothing guarantees the directory, so
    both halves check it rather than trusting their own mkdir."""
    shared = tmp_path / "run"
    shared.mkdir(mode=mode)
    with pytest.raises(session.UnsafeRuntimeDir):
        session.check_runtime_dir(str(shared))


@needs_unix_sockets
def test_a_runtime_dir_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    impostor = tmp_path / "run"
    impostor.write_text("")
    with pytest.raises(session.UnsafeRuntimeDir):
        session.check_runtime_dir(str(impostor))


# --- the frames --------------------------------------------------------------


@pytest.mark.parametrize(
    "items",
    [[], [b""], [b"one"], [b"", b"two", b""], [b"\xff\xfe", b"\x00" * 100]],
)
def test_a_sequence_survives_the_round_trip(items: list[bytes]) -> None:
    assert protocol.unpack_strings(protocol.pack_strings(items)) == items


@pytest.mark.parametrize(
    "payload",
    [b"", b"\x00\x00\x00\x01", b"\x00\x00\x00\x01\x00\x00\x00\x09ab", b"\x00" * 8],
)
def test_a_sequence_this_cannot_read_is_an_error(payload: bytes) -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.unpack_strings(payload)


@pytest.mark.parametrize("stdin", [None, b"", b"select 1;\n", b"\xff\xfe"])
def test_a_request_survives_the_round_trip(stdin: bytes | None) -> None:
    packed = protocol.pack_request(
        argv=["-c", "select 'ü'", "\udcff"],
        cwd="/some/where",
        environ={"NO_COLOR": ""},
        stdin=stdin,
    )
    request = protocol.unpack_request(packed)
    # the surrogate is a file name the filesystem accepts and UTF-8 does not,
    # which `os.fsencode` carries and a plain encode would refuse
    assert request.argv == ["-c", "select 'ü'", "\udcff"]
    assert request.cwd == "/some/where"
    assert request.environ == {"NO_COLOR": ""}
    assert request.stdin == stdin


@pytest.mark.parametrize(
    "stdout_isatty,stderr_isatty",
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_a_request_carries_which_streams_are_terminals(
    stdout_isatty: bool, stderr_isatty: bool
) -> None:
    """`--color auto` reads `sys.stdout.isatty()`, and on the server every
    stream is a buffer, so only the client can answer this."""
    request = protocol.unpack_request(
        protocol.pack_request(
            argv=[],
            cwd="/",
            environ={},
            stdin=None,
            stdout_isatty=stdout_isatty,
            stderr_isatty=stderr_isatty,
        )
    )
    assert request.stdout_isatty is stdout_isatty
    assert request.stderr_isatty is stderr_isatty


def test_a_request_carries_only_the_environment_it_declares() -> None:
    """Not the caller's whole environment: an adapter option's `envvar`
    resolves against the server's, and forwarding one is a credential leak.

    `TERM` is not on the list: nothing in the output path reads it."""
    forwarded = protocol.forwarded_environ(
        {"TERM": "dumb", "PGPASSWORD": "hunter2", "NO_COLOR": ""}
    )
    assert forwarded == {"NO_COLOR": ""}


def test_an_absent_no_color_arrives_absent() -> None:
    """It is read for its presence, so an empty one is not the same as none."""
    assert protocol.forwarded_environ({"TERM": "dumb"}) == {}


def test_frames_survive_a_socket() -> None:
    left, right = socket.socketpair()
    with left, right:
        protocol.send_frame(left, protocol.STDOUT, b"one")
        protocol.send_frame(left, protocol.EXIT)
        left.close()
        assert protocol.recv_frame(right) == (protocol.STDOUT, b"one")
        assert protocol.recv_frame(right) == (protocol.EXIT, b"")
        assert protocol.recv_frame(right) is None


def test_a_connection_that_ends_mid_frame_is_an_error() -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(protocol.HEADER.pack(protocol.STDOUT, 10) + b"abc")
        left.close()
        with pytest.raises(protocol.ProtocolError):
            protocol.recv_frame(right)


def test_a_frame_too_large_to_be_real_is_refused() -> None:
    """A length is four bytes from a peer, so it may not become an allocation."""
    left, right = socket.socketpair()
    with left, right:
        left.sendall(protocol.HEADER.pack(protocol.STDOUT, protocol.MAX_PAYLOAD + 1))
        with pytest.raises(protocol.ProtocolError):
            protocol.recv_frame(right)


# --- the client, against a stub server ---------------------------------------


class StubServer:
    """One connection's worth of a session, built on the real frames.

    Records the request it was sent, so a test can assert what the client
    forwarded as well as what it printed.
    """

    def __init__(
        self,
        path: str,
        *,
        advertises: str,
        frames: Sequence[tuple[int, bytes]],
    ) -> None:
        self.path = path
        self.request: protocol.Request | None = None
        self._version = advertises
        self._frames = list(frames)
        self._closing = False
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(path)
        self._listener.listen(1)
        # so that `close()` is prompt whether or not anyone ever connected
        self._listener.settimeout(0.05)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._closing:
            try:
                connection, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                self._answer(connection)
            return

    def _answer(self, connection: socket.socket) -> None:
        try:
            protocol.send_frame(
                connection, protocol.HELLO, self._version.encode("utf-8")
            )
            frame = protocol.recv_frame(connection)
            if frame is not None and frame[0] == protocol.REQUEST:
                self.request = protocol.unpack_request(frame[1])
            for kind, payload in self._frames:
                protocol.send_frame(connection, kind, payload)
        except OSError:
            # a client that stopped reading -- a version it refused to be
            # served by, or one that died -- which the real server also drops
            return

    def close(self) -> None:
        self._closing = True
        self._thread.join(timeout=5)
        self._listener.close()


Serve = Callable[..., StubServer]


@pytest.fixture
def runtime_dir() -> Iterator[str]:
    """A short-pathed runtime dir: an AF_UNIX path is ~104 bytes on macOS, and
    pytest's `tmp_path` is longer than that on its own."""
    base = tempfile.mkdtemp(prefix="hsql-", dir="/tmp")
    os.mkdir(os.path.join(base, "hsql"), mode=0o700)
    yield base
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def environ(runtime_dir: str) -> dict[str, str]:
    return {"XDG_RUNTIME_DIR": runtime_dir}


@pytest.fixture
def serve(environ: dict[str, str]) -> Iterator[Serve]:
    servers: list[StubServer] = []

    def _serve(
        name: str = "prod",
        *,
        advertises: str = protocol.VERSION,
        frames: Sequence[tuple[int, bytes]] = (),
    ) -> StubServer:
        server = StubServer(
            session.socket_path(name, environ), advertises=advertises, frames=frames
        )
        servers.append(server)
        return server

    yield _serve
    for server in servers:
        server.close()


def ambient(name: str = "prod") -> session.Session:
    return session.Session(name, explicit=False)


def typed(name: str = "prod") -> session.Session:
    return session.Session(name, explicit=True)


@needs_unix_sockets
def test_a_response_reaches_the_caller_byte_for_byte(
    serve: Serve, environ: dict[str, str], capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    """The whole reason bytes cross the wire rather than rows: the client
    cannot format anything, so it cannot disagree with a cold invocation."""
    server = serve(
        frames=[
            (protocol.STDOUT, b" a \n---\n"),
            (protocol.STDERR, b"note: something\n"),
            (protocol.STDOUT, b" 1 \n"),
            (protocol.EXIT, bytes([ExitCode.QUERY])),
        ]
    )
    assert client.run(typed(), ["-c", "select 1"], environ) == ExitCode.QUERY
    captured = capsysbinary.readouterr()
    assert captured.out == b" a \n---\n 1 \n"
    assert captured.err == b"note: something\n"
    # the server got a request it could read, rather than nothing at all
    assert server.request is not None
    assert server.request.argv == ["-c", "select 1"]


@needs_unix_sockets
def test_the_server_is_sent_the_invocation_and_not_the_clients_flag(
    serve: Serve, environ: dict[str, str]
) -> None:
    server = serve(frames=[(protocol.EXIT, b"\x00")])
    assert (
        client.run(
            typed(),
            ["--session", "prod", "-c", "select 1"],
            {**environ, "NO_COLOR": "1", "PGPASSWORD": "hunter2"},
        )
        == ExitCode.OK
    )
    assert server.request is not None
    assert server.request.argv == ["-c", "select 1"]
    assert server.request.cwd == os.getcwd()
    assert server.request.environ == {"NO_COLOR": "1"}
    assert server.request.stdin is None


@needs_unix_sockets
def test_a_dash_f_sends_the_bytes_the_server_has_no_stdin_for(
    serve: Serve, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    server = serve(frames=[(protocol.EXIT, b"\x00")])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"select 2;\n")))
    assert client.run(typed(), ["-f", "-"], environ) == ExitCode.OK
    assert server.request is not None
    assert server.request.stdin == b"select 2;\n"


@pytest.mark.parametrize(
    "argv,reads",
    [
        (["-c", "select 1"], False),
        (["-f", "-"], True),
        (["-f-"], True),
        (["--file", "-"], True),
        (["--file=-"], True),
        (["-f", "script.sql"], False),
        # a short cluster, which is the `-tAc` idiom the skill teaches
        (["-tAf", "-"], True),
        (["-tf", "-"], True),
        (["-Af", "-"], True),
        (["-tf-"], True),
        (["-xrtAf", "-"], True),
        (["-tAf", "script.sql"], False),
        # click gives a short option no `=` form: this is a file named `=-`
        (["-f=-"], False),
        # `-c` takes a value, so the `f` after it is part of that value
        (["-cf", "-"], False),
        (["-of", "-"], False),
        # a connection string called `-` is not a file, and comes after `--`
        (["--", "-"], False),
        (["--", "-f", "-"], False),
    ],
)
def test_which_invocations_need_the_callers_stdin(argv: list[str], reads: bool) -> None:
    assert client._reads_stdin(argv) is reads


@needs_unix_sockets
def test_a_server_from_another_release_is_never_served(
    serve: Serve, environ: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Output bytes are hsql's API, and two releases may not agree about them,
    so this refuses rather than falling back -- ambient or not."""
    serve(advertises="0.0.1", frames=[(protocol.STDOUT, b"nope")])
    assert client.run(ambient(), ["-c", "select 1"], environ) == ExitCode.USAGE
    captured = capsys.readouterr()
    assert not captured.out
    assert "hsql 0.0.1" in captured.err
    assert "Restart the session" in captured.err


@needs_unix_sockets
def test_a_typed_session_that_is_not_running_fails(
    environ: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """A name the caller typed is an assertion; running cold against one would
    silently lose the state they were counting on."""
    assert client.run(typed(), ["-c", "select 1"], environ) == ExitCode.CONNECTION
    assert "no session named 'prod' is running" in capsys.readouterr().err


@needs_unix_sockets
def test_an_ambient_session_that_is_not_running_runs_cold(
    environ: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert client.run(ambient(), ["-c", "select 1"], environ) is None
    err = capsys.readouterr().err
    assert "running cold" in err
    assert "hsql --serve prod" in err


@needs_unix_sockets
def test_a_name_that_could_name_no_session_is_refused(
    environ: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert client.run(typed("../etc"), [], environ) == ExitCode.USAGE
    assert "is not a session name" in capsys.readouterr().err


def test_a_platform_without_unix_sockets_has_no_sessions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Native Windows, exercised wherever the tests run.

    A typed session is a *usage* error there rather than a connection one:
    nothing is down, and a caller who answers an exit 3 by starting the server
    would answer it forever. An ambient one warns and runs cold, like any other.
    """
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)
    assert client.run(typed(), ["-c", "select 1"], {}) == ExitCode.USAGE
    assert "native Windows" in capsys.readouterr().err
    assert client.run(ambient(), ["-c", "select 1"], {}) is None
    assert "running cold" in capsys.readouterr().err


@needs_unix_sockets
def test_a_refused_socket_is_reported_and_left_alone(
    environ: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """A running server also refuses between its `bind()` and its `listen()`,
    and on the BSDs whenever its backlog is full, so a client that deleted the
    file on one refusal would take a live session away from everyone after it.
    The server unlinks a stale socket when it starts."""
    path = session.socket_path("prod", environ)
    bound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound.bind(path)
    bound.close()
    assert os.path.exists(path)
    assert client.run(ambient(), [], environ) is None
    assert "no session named 'prod' is running" in capsys.readouterr().err
    assert os.path.exists(path)


@needs_unix_sockets
def test_a_runtime_dir_that_cannot_hold_a_socket_is_a_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not a traceback: every errno `connect()` can raise other than "nothing
    is listening" used to escape `run()` as one, and exit 1."""
    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("")
    environ = {"XDG_RUNTIME_DIR": str(not_a_directory)}
    assert client.run(typed(), ["-c", "select 1"], environ) == ExitCode.CONNECTION
    assert "is unreachable" in capsys.readouterr().err


@needs_unix_sockets
def test_a_socket_path_too_long_to_bind_is_refused_by_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`sun_path` is 104 bytes on macOS, and the TMPDIR it supplies is ~50 of
    them, so a name well inside MAX_NAME_LENGTH can still be unreachable."""
    environ = {"XDG_RUNTIME_DIR": "/tmp/" + "d" * 100}
    assert client.run(typed("x" * 40), ["-c", "select 1"], environ) == ExitCode.USAGE
    message = capsys.readouterr().err
    assert "over the" in message and str(session.MAX_SOCKET_PATH) in message


@needs_unix_sockets
def test_a_directory_someone_else_could_reach_is_never_connected_to(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """argv can carry a password, so a socket in a directory another user can
    write is one this must not send a request to."""
    shared = tmp_path / "hsql"
    shared.mkdir(mode=0o777)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(shared / "prod.sock"))
    listener.listen(1)
    try:
        assert (
            client.run(typed(), ["-c", "select 1"], {"XDG_RUNTIME_DIR": str(tmp_path)})
            == ExitCode.CONNECTION
        )
    finally:
        listener.close()
    assert "is unreachable" in capsys.readouterr().err


@needs_unix_sockets
def test_an_interrupt_while_waiting_exits_the_way_the_cold_path_does(
    serve: Serve, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(frames=[(protocol.EXIT, b"\x00")])

    def interrupt(_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(client, "_relay", interrupt)
    assert client.run(typed(), ["-c", "select 1"], environ) == ExitCode.INTERRUPT


@needs_unix_sockets
def test_a_server_that_stops_mid_response_is_a_connection_failure(
    serve: Serve, environ: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """No `EXIT` frame is a run whose outcome nobody knows, which is not a
    query that succeeded."""
    serve(frames=[(protocol.STDOUT, b"partial")])
    assert client.run(typed(), [], environ) == ExitCode.CONNECTION
    assert "did not answer" in capsys.readouterr().err


# --- the copies, and what pins them ------------------------------------------


def test_the_clients_exit_codes_are_hsqls() -> None:
    """Copied rather than imported, because reaching `diagnostics` costs more
    than the round trip they report on."""
    assert client.USAGE == ExitCode.USAGE
    assert client.CONNECTION == ExitCode.CONNECTION
    assert client.INTERRUPT == ExitCode.INTERRUPT


def test_the_clients_boolean_short_flags_are_the_commands() -> None:
    """The client has to know which short options take no value, because those
    are the ones a `-f` can follow in a cluster. Copied for the same reason as
    the exit codes, and pinned here against the real command."""
    from harlequin.hsql.cli import bare_command

    boolean_shorts = {
        spelling.lstrip("-")
        for param in bare_command().params
        for spelling in param.opts
        if getattr(param, "is_flag", False)
        and len(spelling) == 2
        and not spelling.startswith("--")
    }
    assert set(client.BOOLEAN_SHORT_FLAGS) == boolean_shorts


def test_the_protocol_version_is_the_release_it_ships_with() -> None:
    """A literal because reading the package metadata costs more than the round
    trip; `scripts/bump_versions.py` is what keeps it true."""
    assert protocol.VERSION == version("harlequin")


def test_a_release_bumps_the_protocol_version_and_rewrites_nothing_else(
    tmp_path: Path,
) -> None:
    source = Path(protocol.__file__)
    copied = tmp_path / "protocol.py"
    copied.write_bytes(source.read_bytes())
    _release_script().write_protocol_version("9.9.9", copied)
    assert copied.read_bytes() == source.read_bytes().replace(
        f'VERSION = "{version("harlequin")}"'.encode(), b'VERSION = "9.9.9"'
    )


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
def test_a_release_leaves_the_files_line_endings_alone(
    ending: bytes, tmp_path: Path
) -> None:
    """A Windows checkout has CRLF here -- `.gitattributes` pins only the
    artifacts whose bytes are a contract -- and a release that normalized them
    would rewrite every line of the file to change one.

    Parametrized rather than left to the platform, so the ending this file does
    *not* have in the checkout running the test is covered too.
    """
    source = Path(protocol.__file__).read_bytes().replace(b"\r\n", b"\n")
    copied = tmp_path / "protocol.py"
    copied.write_bytes(source.replace(b"\n", ending))
    _release_script().write_protocol_version("9.9.9", copied)
    written = copied.read_bytes()
    assert b'VERSION = "9.9.9"' in written
    assert written == source.replace(
        f'VERSION = "{version("harlequin")}"'.encode(), b'VERSION = "9.9.9"'
    ).replace(b"\n", ending)


def _release_script() -> ModuleType:
    """The release script, loaded from `scripts/`, which is not a package."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "bump_versions.py"
    spec = importlib.util.spec_from_file_location("bump_versions", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the whole thing, from the console script --------------------------------

HsqlProcess = Callable[..., "subprocess.CompletedProcess[str]"]


@pytest.fixture
def hsql_process(tmp_path: Path) -> HsqlProcess:
    """Run `main()` the way the console script does, in a fresh interpreter.

    A clean machine, too: the child gets an empty directory as its cwd, its
    home and its config dir, so it reads no config file of whoever is running
    the tests -- and no `HSQL_SESSION` they happen to have set.
    """

    def _run(argv: Sequence[str], **environ: str) -> subprocess.CompletedProcess[str]:
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
            text=True,
            cwd=tmp_path,
            env={
                **{k: v for k, v in os.environ.items() if k != "HSQL_SESSION"},
                "HOME": str(tmp_path),
                "USERPROFILE": str(tmp_path),
                "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
                "APPDATA": str(tmp_path / "appdata"),
                "LOCALAPPDATA": str(tmp_path / "localappdata"),
                **environ,
            },
        )

    return _run


def test_an_ambient_session_that_is_down_still_runs_the_query(
    hsql_process: HsqlProcess,
) -> None:
    """The fallback is the one behavior that must not break a script."""
    proc = hsql_process(
        ["-c", "select 1", "--no-init", ":memory:"],
        HSQL_SESSION="nobody-started-this",
    )
    assert proc.returncode == ExitCode.OK
    assert "1" in proc.stdout
    assert "running cold" in proc.stderr


def test_a_typed_session_that_is_down_runs_nothing(hsql_process: HsqlProcess) -> None:
    proc = hsql_process(["--session", "nobody", "-c", "select 1"])
    # a platform that has no sessions at all is a usage error, not a server that
    # happens to be down
    assert proc.returncode == (
        ExitCode.CONNECTION if HAS_UNIX_SOCKETS else ExitCode.USAGE
    )
    assert not proc.stdout


def test_a_session_flag_with_no_value_names_itself(
    hsql_process: HsqlProcess,
) -> None:
    """The client refuses it rather than click, which does not know the option
    until PR 2 declares it and would answer "No such option"."""
    proc = hsql_process(["--session"])
    assert proc.returncode == ExitCode.USAGE
    assert "--session needs a name" in proc.stderr
    assert not proc.stdout
