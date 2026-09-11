"""The half of a warm session that runs in the caller's process.

**Stdlib only, and that is the load-bearing constraint of the whole feature.**
A warm round trip is about a millisecond; everything else a caller waits for is
this process starting. `import click` costs more than the round trip and
`harlequin.config` more again, so a client that reached for either would spend
the win before it made the call.

So this module parses nothing. It scans argv for the three things it cannot
avoid knowing about -- its own `--session`, a `-f -` whose bytes only it can
read, and a `--session-status`, which the server reports on itself rather
than running -- and forwards the rest opaquely, to be parsed by the same
command the cold path builds. `hsql --session prod --badflag` gets the same message
and the same exit code as `hsql --badflag`, because it is the same code.

Diagnostics go straight to stderr rather than through
`harlequin.hsql.diagnostics`, which costs more to import than the round trip it
would report on. Nothing is lost: that module exists to redact, and this one
holds no secret to redact -- it reads no profile, loads no adapter and never
sees a connection string. The server's own stderr arrives already redacted, and
is copied through untouched.
"""

from __future__ import annotations

import os
import socket
import sys

from harlequin.hsql import protocol
from harlequin.hsql.session import (
    MAX_NAME_LENGTH,
    MAX_SOCKET_PATH,
    SESSION_OPTION,
    UnsafeRuntimeDir,
    check_runtime_dir,
    is_valid_name,
    requests_status,
    socket_path,
    without_session_option,
)

TYPE_CHECKING = False
"""Every annotation here is a string (PEP 563), so `typing` stays off this path."""

if TYPE_CHECKING:
    from typing import Mapping, Sequence, TextIO

    from harlequin.hsql.session import Session

USAGE = 2
CONNECTION = 3
INTERRUPT = 130
"""`ExitCode`'s three, copied rather than imported.

`harlequin.hsql.diagnostics` costs more to reach than the round trip they
report on. `tests/unit_tests/test_hsql_session.py` pins all three to the enum.
"""

STDIN_ARGUMENT = "-"
FILE_OPTION = "--file"
FILE_SHORT = "f"
"""The one per-request flag the client has to understand: the server has no
stdin, so bytes a caller piped in have to travel with the request."""

BOOLEAN_SHORT_FLAGS = "tArx"
"""hsql's short options that take no value, so a cluster can carry `-f` after
them: `-tAf -` is one idiom, not three flags.

Any other short option consumes the rest of its cluster as its value, so an `f`
behind one is that value and not this flag. `tests/unit_tests/test_hsql_session.py`
pins this against the real command.
"""


def run(
    session: "Session", argv: "Sequence[str]", environ: "Mapping[str, str]"
) -> "int | None":
    """Serve this invocation from a session, or return None to run it cold.

    None is only ever returned for an *ambient* session -- one named by
    `HSQL_SESSION` rather than typed -- and never silently: the warning that
    this invocation is running cold is not suppressible, because "it got slow
    and my temp tables vanished" must not be something a caller has to guess
    at.
    """
    refusal = cannot_be_served(session, environ)
    if refusal is not None:
        return _no_session(session, refusal[0], remedy=refusal[1], code=refusal[2])

    path = socket_path(session.name, environ)
    try:
        check_runtime_dir(os.path.dirname(path))
        connection = _connect(path)
    except FileNotFoundError:
        # no runtime directory at all, so no session has ever run here
        connection = None
    except (UnsafeRuntimeDir, OSError) as e:
        return _no_session(
            session, f"a session named {session.name!r} is unreachable: {e}"
        )
    if connection is None:
        return _no_session(
            session,
            f"no session named {session.name!r} is running",
            remedy=f" Start one with `hsql --serve {session.name} ...`.",
        )
    try:
        return _exchange(connection, session, argv, environ)
    except KeyboardInterrupt:
        # what the cold path exits with, and silently, for the same reason
        return INTERRUPT
    except (protocol.ProtocolError, OSError) as e:
        _error(f"the session named {session.name!r} did not answer: {e}")
        return CONNECTION
    finally:
        connection.close()


def cannot_be_served(
    session: "Session", environ: "Mapping[str, str]"
) -> "tuple[str, str, int] | None":
    """Why this invocation can never reach a session, before one is looked for.

    A reason, a remedy and an exit code -- all `USAGE`, because none of them is
    a server that happens to be down, and a caller who answered an exit 3 by
    starting one would answer it forever. The server asks the same question of
    the name it was given, so that a name no client could reach is refused
    before a connection is paid for.

    Ordered most specific first. A flag with no value is the caller's typo on
    every platform, so it is named before the platform is: telling someone on
    Windows that sessions need a unix socket, when what they did was leave
    `--session` empty, answers a question they did not ask.
    """
    if not session.name:
        # only a typed `--session` with nothing after it gets here: an empty
        # environment variable names no session at all
        return (
            f"{SESSION_OPTION} needs a name",
            f" Pass one: `hsql {SESSION_OPTION} prod ...`.",
            USAGE,
        )
    if not hasattr(socket, "AF_UNIX"):
        # native Windows has no AF_UNIX in CPython. WSL2 is Linux and gets the
        # feature, which is why the docs say *native* Windows.
        return (
            "hsql sessions need a unix socket, which native Windows has not",
            "",
            USAGE,
        )
    if not is_valid_name(session.name):
        return (
            f"{session.name!r} is not a session name",
            " A name is letters, digits, underscores and dashes, up to "
            f"{MAX_NAME_LENGTH} of them.",
            USAGE,
        )
    length = len(os.fsencode(socket_path(session.name, environ)))
    if length >= MAX_SOCKET_PATH:
        return (
            f"the socket path for {session.name!r} is {length} bytes, over the "
            f"{MAX_SOCKET_PATH} a unix socket takes",
            " Use a shorter name, or set XDG_RUNTIME_DIR to a shorter directory.",
            USAGE,
        )
    return None


def _connect(path: str) -> "socket.socket | None":
    """The session listening at `path`, or None if nothing is listening there.

    A refused connection is reported and not cleaned up. The socket file
    belongs to the server, which unlinks a stale one when it starts: a running
    server also refuses between its `bind()` and its `listen()`, and on the
    BSDs whenever its backlog is full, so a client that deleted the file on one
    refusal would take a live session away from everyone after it.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(path)
    except (FileNotFoundError, ConnectionRefusedError):
        sock.close()
        return None
    except BaseException:
        sock.close()
        raise
    return sock


def _exchange(
    connection: "socket.socket",
    session: "Session",
    argv: "Sequence[str]",
    environ: "Mapping[str, str]",
) -> int:
    """Hand the server this invocation, and copy back what it writes."""
    greeting = protocol.recv_frame(connection)
    if greeting is None or greeting[0] != protocol.HELLO:
        raise protocol.ProtocolError("it did not introduce itself")
    served_by = greeting[1].decode("utf-8", "replace")
    if served_by != protocol.VERSION:
        # refused rather than served, ambient or not: hsql's output bytes are
        # its API, and two releases may not agree about them
        _error(
            f"the session named {session.name!r} is hsql {served_by}, and this "
            f"is hsql {protocol.VERSION}. Restart the session."
        )
        return USAGE

    if requests_status(argv):
        # not a request, so it takes no turn at the session's connection
        protocol.send_frame(
            connection,
            protocol.STATUS,
            protocol.pack_status(without_session_option(argv)),
        )
        return _relay(connection)

    stdin = _stdin_for(argv)
    if stdin is not None and len(stdin) > protocol.MAX_PAYLOAD:
        _error(
            f"the SQL on stdin is {len(stdin)} bytes, over the "
            f"{protocol.MAX_PAYLOAD} a session request carries. Run it without "
            "a session."
        )
        return USAGE

    protocol.send_frame(
        connection,
        protocol.REQUEST,
        protocol.pack_request(
            argv=without_session_option(argv),
            cwd=os.getcwd(),
            environ=protocol.forwarded_environ(environ),
            stdin=stdin,
            stdout_isatty=_isatty(sys.stdout),
            stderr_isatty=_isatty(sys.stderr),
        ),
    )
    return _relay(connection)


def _isatty(stream: "TextIO") -> bool:
    """Whether `stream` is a terminal, which only this side can answer.

    `--color auto` reads it, and on the server every stream is a buffer.
    """
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        # a closed or replaced stream: not a terminal, which is the safe answer
        return False


def _relay(connection: "socket.socket") -> int:
    """Copy the server's streams to this process's, and return its exit code."""
    while True:
        frame = protocol.recv_frame(connection)
        if frame is None:
            raise protocol.ProtocolError("it stopped before saying how it went")
        kind, payload = frame
        if kind == protocol.STDOUT:
            sys.stdout.buffer.write(payload)
        elif kind == protocol.STDERR:
            # stdout first, for the reason `_write()` gives
            sys.stdout.flush()
            sys.stderr.buffer.write(payload)
            sys.stderr.flush()
        elif kind == protocol.EXIT:
            sys.stdout.flush()
            return int.from_bytes(payload, "big")
        else:
            raise protocol.ProtocolError(f"it sent a frame this cannot read: {kind}")


def _stdin_for(argv: "Sequence[str]") -> "bytes | None":
    """This process's stdin, when `-f -` asked for it. The server has none."""
    return sys.stdin.buffer.read() if _reads_stdin(argv) else None


def _reads_stdin(argv: "Sequence[str]") -> bool:
    """Whether any `-f`/`--file` in `argv` names stdin, as click would read it."""
    for index, argument in enumerate(argv):
        if argument == "--":
            break
        following = argv[index + 1 : index + 2]
        if argument == FILE_OPTION:
            if following == [STDIN_ARGUMENT]:
                return True
        elif argument == f"{FILE_OPTION}={STDIN_ARGUMENT}":
            return True
        elif _short_cluster_reads_stdin(argument, following):
            return True
    return False


def _short_cluster_reads_stdin(argument: str, following: "Sequence[str]") -> bool:
    """Whether a short-option cluster like `-tAf -` or `-tf-` names stdin.

    click reads a cluster left to right and hands the rest of it to the first
    option that takes a value, so an `f` behind any option but a boolean flag
    is that option's value rather than `--file`. There is no `=` form for a
    short option: click reads `-f=-` as the file named `=-`.
    """
    if not argument.startswith("-") or argument.startswith("--"):
        return False
    for position, letter in enumerate(argument[1:], start=2):
        if letter == FILE_SHORT:
            rest = argument[position:]
            return rest == STDIN_ARGUMENT or (
                not rest and list(following) == [STDIN_ARGUMENT]
            )
        if letter not in BOOLEAN_SHORT_FLAGS:
            # an option that takes a value; the rest of the cluster is that value
            return False
    return False


def _no_session(
    session: "Session", reason: str, *, remedy: str = "", code: int = CONNECTION
) -> "int | None":
    """Refuse a typed session, or warn and run cold for an ambient one.

    A value the caller actually typed carries intent that an environment
    variable does not -- the same rule `merge_profile_with_cli()` reads a
    command line by -- so `--session` fails where `HSQL_SESSION` falls back.
    """
    if session.explicit:
        _error(f"{reason}.{remedy}")
        return code
    _note(f"{reason}, so this invocation is running cold.{remedy}")
    return None


def _error(message: str) -> None:
    _write(f"hsql: error: {message}")


def _note(message: str) -> None:
    _write(f"note: {message}")


def _write(line: str) -> None:
    # stdout first, as `diagnostics._write()` does: it is block-buffered when
    # it is a pipe and stderr is not, so a diagnostic written now would
    # otherwise overtake whatever it describes.
    sys.stdout.flush()
    print(line, file=sys.stderr)
