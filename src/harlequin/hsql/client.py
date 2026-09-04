"""The half of a warm session that runs in the caller's process.

**Stdlib only, and that is the load-bearing constraint of the whole feature.**
A warm round trip is about a millisecond; everything else a caller waits for is
this process starting. `import click` costs more than the round trip and
`harlequin.config` more again, so a client that reached for either would spend
the win before it made the call.

So this module parses nothing. It scans argv for the two things it cannot avoid
knowing about -- its own `--session`, and a `-f -` whose bytes only it can read
-- and forwards the rest opaquely, to be parsed by the same command the cold
path builds. `hsql --session prod --badflag` gets the same message and the same
exit code as `hsql --badflag`, because it is the same code.

Diagnostics go straight to stderr rather than through
`harlequin.hsql.diagnostics`, which costs ~68ms to import. Nothing is lost:
that module exists to redact, and this one holds no secret to redact -- it
reads no profile, loads no adapter and never sees a connection string. The
server's own stderr arrives already redacted, and is copied through untouched.
"""

from __future__ import annotations

import os
import socket
import sys

from harlequin.hsql import protocol
from harlequin.hsql.session import (
    MAX_NAME_LENGTH,
    is_valid_name,
    socket_path,
    without_session_option,
)

TYPE_CHECKING = False
"""`typing` costs ~10ms, and every annotation here is a string (PEP 563)."""

if TYPE_CHECKING:
    from typing import Mapping, Sequence

    from harlequin.hsql.session import Session

USAGE = 2
CONNECTION = 3
"""`ExitCode.USAGE` and `ExitCode.CONNECTION`, copied rather than imported.

Reaching `harlequin.hsql.diagnostics` for them would cost ~68ms, more than twice
the round trip they report on. `tests/unit_tests/test_hsql_session.py` pins both
to the enum.
"""

STDIN_ARGUMENT = "-"
FILE_OPTIONS = ("-f", "--file")
"""The one per-request flag the client has to understand: the server has no
stdin, so bytes a caller piped in have to travel with the request."""


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
    if not hasattr(socket, "AF_UNIX"):
        # native Windows has no AF_UNIX in CPython, through 3.14. WSL2 is Linux
        # and gets the feature, which is why the docs say *native* Windows.
        #
        # A usage error rather than a connection one: nothing is down, and a
        # caller who retries an exit 3 by starting the server would retry here
        # forever. Same code as a name that could never name a session, for the
        # same reason -- both say this invocation can never be served.
        return _no_session(
            session,
            "hsql sessions need a unix socket, which native Windows has not",
            code=USAGE,
        )
    if not is_valid_name(session.name):
        return _no_session(
            session,
            f"{session.name!r} is not a session name",
            remedy=(
                " A name is letters, digits, underscores and dashes, up to "
                f"{MAX_NAME_LENGTH} of them."
            ),
            code=USAGE,
        )

    connection = _connect(socket_path(session.name, environ))
    if connection is None:
        return _no_session(
            session,
            f"no session named {session.name!r} is running",
            remedy=f" Start one with `hsql --serve {session.name} ...`.",
        )
    try:
        return _exchange(connection, session, argv, environ)
    except (protocol.ProtocolError, OSError) as e:
        _error(f"the session named {session.name!r} did not answer: {e}")
        return CONNECTION
    finally:
        connection.close()


def _connect(path: str) -> "socket.socket | None":
    """The session listening at `path`, or None if nothing is listening there.

    A socket file whose server is gone refuses the connection, so this is also
    where a stale one is unlinked -- by the client, since it is the process
    that discovered it.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(path)
    except FileNotFoundError:
        sock.close()
        return None
    except ConnectionRefusedError:
        sock.close()
        _unlink_stale(path)
        return None
    except OSError:
        sock.close()
        raise
    return sock


def _unlink_stale(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        # someone else's to clean up, or already gone. Either way nothing is
        # listening here, which is what the caller is about to be told.
        pass


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

    protocol.send_frame(
        connection,
        protocol.REQUEST,
        protocol.pack_request(
            argv=without_session_option(argv),
            cwd=os.getcwd(),
            environ=protocol.forwarded_environ(environ),
            stdin=_stdin_for(argv),
        ),
    )
    return _relay(connection)


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
    for index, argument in enumerate(argv):
        if argument == "--":
            break
        # `-f-`, which click reads as a short option and its value
        if argument == f"-f{STDIN_ARGUMENT}":
            return True
        if argument in FILE_OPTIONS and argv[index + 1 : index + 2] == [STDIN_ARGUMENT]:
            return True
        if any(argument == f"{option}={STDIN_ARGUMENT}" for option in FILE_OPTIONS):
            return True
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
