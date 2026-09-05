"""The half of a warm session that holds the connection: `hsql --serve NAME`.

A foreground process that connects once and then answers invocations sent to
it over a unix socket, so that a caller pays neither the imports nor the
connection again. What it runs is the same command the cold path builds --
`harlequin.hsql.cli.run()`, with the request's argv -- into a recorder that
stands in for the process's streams, and what it sends back is the bytes that
recorder holds and the code the command exited with. The server formats
nothing and parses nothing of its own, which is what keeps a served invocation
byte-for-byte a cold one.

A session is one connection, so requests run **one at a time**: the adapter
contract says nothing about thread-safety, and the IDE has never needed it. A
second client waits its turn, bounded by `--queue-timeout`, and is told it
never reached the database if that runs out -- a different fact from a query
that ran too long. `--session-status` is the exception: it arrives as its own
frame rather than as an invocation, and the server reports from its own
bookkeeping while a request runs.

The name is the caller's and the **identity is the server's**: the connection
options it resolved at start-up are what a served request's own connection
options are compared against. Equal values are served; differing ones exit 2.

A running server is a live, authenticated connection that anything able to
write its socket can use, so the socket is the credential: `AF_UNIX` only, in
a directory only this user can reach, and every peer's uid is checked on
accept. Native Windows has no `AF_UNIX`, so this is POSIX only; WSL2 is Linux
and gets it.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import socket
import struct
import sys
import threading
import time
import traceback
from collections import deque
from itertools import count
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Sequence

from harlequin.exception import HarlequinConnectionError
from harlequin.hsql import diagnostics, protocol
from harlequin.hsql.diagnostics import PROGRAM, ExitCode
from harlequin.hsql.session import (
    STATUS_OPTION,
    UnsafeRuntimeDir,
    check_runtime_dir,
    socket_path,
)
from harlequin.redact import redact_text

if TYPE_CHECKING:
    from pathlib import Path

    from harlequin.adapter import HarlequinConnection
    from harlequin.options import AbstractOption

ACCEPT_POLL_SECONDS = 0.5
"""How often the accept loop looks up from the socket to see if it was stopped."""

REPEATED_SIGNAL_SECONDS = 1.0
"""A second stop signal this long after the first is an operator who wants
out now, rather than the same Ctrl-C arriving twice."""

BACKLOG = 16
"""Connections the kernel queues before the accept loop gets to them."""

LOCK_SUFFIX = ".lock"
"""Beside the socket, and held for the server's lifetime: the one thing that
tells a live session from a stale socket file without racing another server
starting under the same name."""


class SessionRunning(Exception):
    """A server is already up under this name."""


class Turnstile:
    """One request at a time, in the order they arrived.

    Hands out tickets, so the client that has waited longest goes next and a
    waiter can give up on a deadline of its own.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._waiting: deque[int] = deque()
        self._tickets = count()
        self._busy = False

    def snapshot(self) -> "tuple[bool, int]":
        """Whether a request holds the connection, and how many wait.

        One acquisition, so the two values describe the same instant.
        """
        with self._condition:
            return self._busy, len(self._waiting)

    def enter(self, timeout: float | None) -> bool:
        """Wait for a turn, and return whether one came before `timeout`."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            ticket = next(self._tickets)
            self._waiting.append(ticket)
            while self._busy or self._waiting[0] != ticket:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._waiting.remove(ticket)
                    self._condition.notify_all()
                    return False
                self._condition.wait(remaining)
            self._waiting.popleft()
            self._busy = True
            return True

    def leave(self) -> None:
        with self._condition:
            self._busy = False
            self._condition.notify_all()


class Recorder:
    """A request's two streams, in the order the command wrote to them.

    One list rather than two buffers, so that a note written between two
    result sets reaches the caller's terminal between them, as it does cold.
    """

    def __init__(self, *, stdout_isatty: bool = False, stderr_isatty: bool = False):
        self.segments: list[tuple[int, bytearray]] = []
        self._stdout_isatty = stdout_isatty
        self._stderr_isatty = stderr_isatty

    def write(self, kind: int, data: bytes) -> None:
        if self.segments and self.segments[-1][0] == kind:
            self.segments[-1][1].extend(data)
        elif data:
            self.segments.append((kind, bytearray(data)))

    def stdout(self) -> io.TextIOWrapper:
        """A stand-in for `sys.stdout`, whose `.buffer` is what `-o -` writes.

        `newline="\n"` is what CPython builds `sys.stdout` with on POSIX, and
        the bytes are hsql's contract: the default would translate every `\n`
        this writes to `os.linesep`.
        """
        return io.TextIOWrapper(
            _Stream(self, protocol.STDOUT, isatty=self._stdout_isatty),
            encoding="utf-8",
            newline="\n",
            write_through=True,
        )

    def stderr(self) -> io.TextIOWrapper:
        return io.TextIOWrapper(
            _Stream(self, protocol.STDERR, isatty=self._stderr_isatty),
            encoding="utf-8",
            errors="backslashreplace",
            newline="\n",
            write_through=True,
        )


class _Stream(io.RawIOBase):
    """One of a recorder's streams, as a raw binary stream."""

    def __init__(self, recorder: Recorder, kind: int, *, isatty: bool) -> None:
        super().__init__()
        self._recorder = recorder
        self._kind = kind
        self._isatty = isatty

    @property
    def name(self) -> str:
        return "<session>"

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        view = memoryview(data)
        self._recorder.write(self._kind, view.tobytes())
        return view.nbytes

    def isatty(self) -> bool:
        return self._isatty


class Served:
    """What the command is told about the session answering it."""

    def __init__(self, server: Server, request: protocol.Request) -> None:
        self._server = server
        self.name = server.name
        self.adapter = server.adapter
        self.identity = server.identity
        self.stdin = request.stdin

    def connection(self) -> HarlequinConnection:
        """The session's connection.

        Raises: HarlequinConnectionError if the session has none to offer.
        """
        return self._server.connection()

    def reset(self) -> None:
        """Close the session's connection and open a fresh one.

        Raises: HarlequinConnectionError if the new one could not be opened.
        """
        self._server.reset()

    def abandon(self) -> None:
        """Note that a cancelled run outlasted its grace period.

        The session then offers no connection until it is reset: the next
        request would otherwise run on one another thread still holds.
        """
        self._server.abandon()


class Server:
    """One session: a name, a connection, and the socket that reaches it."""

    def __init__(
        self,
        name: str,
        *,
        adapter: str,
        connection: HarlequinConnection,
        reconnect: Callable[[], HarlequinConnection],
        identity: Mapping[str, Any] | None = None,
        options: Sequence[AbstractOption] | None = None,
        ssh: str | None = None,
        queue_timeout: float | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.adapter = adapter
        self.identity: Mapping[str, Any] = {"adapter": adapter, **(identity or {})}
        """The connection options this session resolved at start-up, and what
        a served request's own are compared against."""
        self._adapter_options = options
        """What the adapter declares, so that `secret=` decides what prints."""
        self._ssh = ssh
        self._started = time.monotonic()
        self._connection: HarlequinConnection | None = connection
        self._connection_error: str | None = None
        self._reconnect = reconnect
        self._abandoned = False
        self._queue_timeout = queue_timeout
        self._environ = dict(os.environ if environ is None else environ)
        self._cwd = os.getcwd()
        self._turnstile = Turnstile()
        self._stopping = threading.Event()
        self._stop_asked_at: float | None = None
        self._requests = 0
        self._stderr = sys.stderr
        self._lock: io.TextIOWrapper | None = None

    @property
    def requests(self) -> int:
        """How many requests this session has answered."""
        return self._requests

    def status(self) -> "dict[str, Any]":
        """This session's state, ready to be JSON.

        The connection string is masked by span and every other value by what
        its adapter declared `secret=`.
        """
        from harlequin.redact import redact_conn_str, redact_profile

        identity = dict(self.identity)
        conn_str = identity.pop("conn_str", ()) or ()
        if isinstance(conn_str, str):
            conn_str = (conn_str,)
        busy, queued = self._turnstile.snapshot()
        return {
            "session": self.name,
            "pid": os.getpid(),
            "version": protocol.VERSION,
            "adapter": identity.pop("adapter", self.adapter),
            "connection": " ".join(redact_conn_str(list(conn_str))) or None,
            "connection_options": redact_profile(identity, self._adapter_options),
            "uptime_s": round(time.monotonic() - self._started, 1),
            "requests": self._requests,
            "state": self._state(busy),
            "queued": queued,
            "transaction_mode": self._transaction_mode(),
            "ssh": self._ssh,
            # null until --idle-timeout and --max-lifetime can answer them
            "idle_timeout_s": None,
            "expires_in_s": None,
        }

    def _state(self, busy: bool) -> str:
        if self._abandoned or self._connection is None:
            return "unavailable"
        return "busy" if busy else "idle"

    def _transaction_mode(self) -> "str | None":
        """The label of the session's transaction mode, or None if it is busy.

        The one field read from the connection, so it takes a turn rather than
        reading beside a request -- the adapter contract says nothing about
        thread-safety. `enter(0)` returns immediately when a request holds it.
        """
        if not self._turnstile.enter(0):
            return None
        try:
            connection = self._connection
            if connection is None:
                return None
            try:  # adapters are third-party code
                mode = connection.transaction_mode
            except Exception:
                return None
            return None if mode is None else mode.label
        finally:
            self._turnstile.leave()

    def stop(self) -> None:
        """Ask the accept loop to finish, from any thread."""
        self._stopping.set()

    def serve(self) -> ExitCode:
        """Listen until stopped, and return the code the process exits with.

        Stopping is a signal, or `stop()`: the socket comes down first so a
        new client is told nothing is listening, requests already accepted
        are answered, and then the connection is closed.
        """
        path = os.path.abspath(socket_path(self.name, self._environ))
        # the handler is installed before the socket is listening, or a stop
        # signal in the window between `listen()` and the handler would kill a
        # process a client can already reach
        with self._stopping_on_signal():
            try:
                listener = self._listen(path)
            except SessionRunning:
                diagnostics.error(
                    f"a session named {self.name!r} is already running.",
                    stream=self._stderr,
                )
                return ExitCode.USAGE
            except (UnsafeRuntimeDir, OSError) as e:
                diagnostics.error(
                    f"a session named {self.name!r} cannot listen: {e}",
                    stream=self._stderr,
                )
                return ExitCode.USAGE

            _warm_imports()
            diagnostics.report_session_ready(
                self.name, self.adapter, stream=self._stderr
            )
            try:
                self._accept(listener)
            finally:
                listener.close()
                with contextlib.suppress(OSError):
                    os.unlink(path)
                # every request already accepted is answered before the
                # connection goes away under it
                self._turnstile.enter(None)
                self._close_connection()
                self._unlock()
        diagnostics.report_session_stopped(
            self.name, self._requests, stream=self._stderr
        )
        if self._abandoned:
            from harlequin.hsql.timeout import halt

            halt(ExitCode.OK)
        return ExitCode.OK

    # --- the session's connection ---------------------------------------------

    def connection(self) -> HarlequinConnection:
        reset = f"run `hsql --session {self.name} --session-reset` to reconnect"
        if self._abandoned:
            raise HarlequinConnectionError(
                f"the session named {self.name!r} cancelled a query that did not "
                f"stop, so its connection cannot be used; {reset}.",
                title="hsql session unavailable",
            )
        if self._connection is None:
            raise HarlequinConnectionError(
                f"the session named {self.name!r} has no connection: "
                f"{self._connection_error}; {reset}.",
                title="hsql session unavailable",
            )
        return self._connection

    def reset(self) -> None:
        self._close_connection()
        self._abandoned = False
        self._connection_error = None
        # where the server started, not where the client is: a relative path
        # in the adapter's options resolves the way it did the first time
        with _in_directory(self._cwd):
            try:
                self._connection = self._reconnect()
            except HarlequinConnectionError as e:
                self._connection_error = e.msg
                raise

    def abandon(self) -> None:
        self._abandoned = True

    def _close_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None or self._abandoned:
            # an abandoned connection belongs to the thread still inside it
            return
        with contextlib.suppress(Exception):  # adapters are third-party code
            connection.close()

    # --- the socket ------------------------------------------------------------

    def _listen(self, path: str) -> socket.socket:
        """Claim the name and bind its socket.

        Raises: SessionRunning if another server holds the name,
        UnsafeRuntimeDir if the directory is not this user's alone, and
        OSError for anything the filesystem or the kernel refused.
        """
        directory = os.path.dirname(path)
        with contextlib.suppress(FileExistsError):
            os.mkdir(directory, 0o700)
        check_runtime_dir(directory)
        self._lock_name(path[: -len(".sock")] + LOCK_SUFFIX)
        # stale, since the lock is ours: whatever server left it is gone
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(path)
            # honored on Linux and macOS, though the directory's mode is the
            # control every kernel enforces
            os.chmod(path, 0o600)
            listener.listen(BACKLOG)
        except BaseException:
            listener.close()
            raise
        return listener

    def _lock_name(self, lock_path: str) -> None:
        import fcntl

        lock = open(lock_path, "w")  # noqa: SIM115 -- held for the server's lifetime
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise SessionRunning(lock_path) from None
        self._lock = lock

    def _unlock(self) -> None:
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def _accept(self, listener: socket.socket) -> None:
        listener.settimeout(ACCEPT_POLL_SECONDS)
        while not self._stopping.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            uid = peer_uid(connection)
            if uid is not None and uid != os.getuid():
                # only reachable through a directory that is not this user's
                # alone, which both halves refuse to use; belt and braces
                diagnostics.report_peer_refused(uid, stream=self._stderr)
                connection.close()
                continue
            threading.Thread(
                target=self._attend,
                args=(connection,),
                name="hsql-request",
                daemon=True,
            ).start()

    @contextlib.contextmanager
    def _stopping_on_signal(self) -> Iterator[None]:
        """Stop on `SIGINT` or `SIGTERM`, and exit at once on a repeated one.

        Repeated after a moment, that is: a terminal's Ctrl-C reaches every
        process in the foreground group, and a wrapper that then signals its
        child delivers two within milliseconds, which is one operator asking
        once. Only the main thread can install a handler; anywhere else,
        `stop()` is the way to stop.
        """
        from harlequin.hsql.timeout import halt

        def handle(number: int, frame: Any) -> None:
            now = time.monotonic()
            if (
                self._stop_asked_at is not None
                and now - self._stop_asked_at > REPEATED_SIGNAL_SECONDS
            ):
                halt(ExitCode.INTERRUPT)
            if self._stop_asked_at is None:
                self._stop_asked_at = now
            self._stopping.set()

        replaced: list[tuple[signal.Signals, Any]] = []
        for number in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(ValueError, OSError):
                replaced.append((number, signal.signal(number, handle)))
        try:
            yield
        finally:
            for number, previous in replaced:
                if previous is None:
                    continue
                with contextlib.suppress(ValueError, OSError, TypeError):
                    signal.signal(number, previous)

    # --- one request -----------------------------------------------------------

    def _attend(self, connection: socket.socket) -> None:
        """Answer one client, on its own thread, in its turn."""
        with connection:
            try:
                protocol.send_frame(
                    connection, protocol.HELLO, protocol.VERSION.encode("utf-8")
                )
                # no clock on this read: a `-f -` sends the bytes a human is
                # still typing, and a client that stalls holds a daemon thread
                # rather than a turn at the connection
                frame = protocol.recv_frame(connection)
                if frame is None:
                    # a client that refused to be served by this version
                    return
                if frame[0] == protocol.STATUS:
                    self._send_status(connection, protocol.unpack_status(frame[1]))
                    return
                if frame[0] != protocol.REQUEST:
                    raise protocol.ProtocolError(f"expected a request, got {frame[0]}")
                request = protocol.unpack_request(frame[1])
            except (protocol.ProtocolError, OSError) as e:
                diagnostics.report_bad_request(str(e), stream=self._stderr)
                return

            if not self._turnstile.enter(self._queue_timeout):
                recorder = Recorder()
                diagnostics.report_queue_timeout(
                    self._queue_timeout or 0, stream=recorder.stderr()
                )
                self._answer(connection, recorder.segments, ExitCode.TIMEOUT)
                return
            started = time.monotonic()
            try:
                segments, code = self._run(request)
            finally:
                self._turnstile.leave()
            self._requests += 1
            elapsed_ms = round((time.monotonic() - started) * 1000)
            self._answer(connection, segments, code)
            diagnostics.report_request(
                self._requests, code, elapsed_ms, stream=self._stderr
            )

    def _send_status(self, connection: socket.socket, argv: Sequence[str]) -> None:
        """Send the status document, without waiting for the connection.

        A served invocation borrows this process's cwd, environment and
        streams for its whole run, so this is built here rather than parsed
        beside one.
        """
        recorder = Recorder()
        try:
            code = self._status_into(recorder, argv)
        except Exception as e:  # noqa: BLE001 -- the server outlives a request
            # what a bug in the request path produces, rather than a traceback
            # out of this thread and into whichever request's recorder holds
            # the process's stderr
            diagnostics.note(
                f"status failed: {redact_text(traceback.format_exc())}",
                stream=self._stderr,
            )
            diagnostics.report_crash(_crash_report(e, argv), stream=recorder.stderr())
            code = ExitCode.CRASH
        self._answer(connection, recorder.segments, code)
        diagnostics.report_status(code, stream=self._stderr)

    def _status_into(self, recorder: Recorder, argv: Sequence[str]) -> ExitCode:
        """Write the status document, or refuse a flag typed beside it.

        The request carries the invocation's argv, since no parser sees one.
        """
        extra = next((argument for argument in argv if argument != STATUS_OPTION), None)
        if extra is not None:
            diagnostics.error(
                f"{STATUS_OPTION} is a flag and takes no value."
                if extra.startswith(STATUS_OPTION + "=")
                else (
                    f"{STATUS_OPTION} reports on the server and takes no other "
                    f"options. Drop {extra}, or run it without "
                    f"{STATUS_OPTION}."
                ),
                stream=recorder.stderr(),
            )
            return ExitCode.USAGE
        print(
            json.dumps(self.status(), separators=(",", ":"), default=str),
            file=recorder.stdout(),
        )
        return ExitCode.OK

    def _run(
        self, request: protocol.Request
    ) -> tuple[list[tuple[int, bytearray]], int]:
        """Run one invocation as the cold path would, into a recorder."""
        from harlequin.hsql import cli

        recorder = Recorder(
            stdout_isatty=request.stdout_isatty, stderr_isatty=request.stderr_isatty
        )
        try:
            with self._as(request, recorder):
                code = cli.run(request.argv, served=Served(self, request))
        except NotADirectoryError as e:
            diagnostics.error(str(e), stream=recorder.stderr())
            return recorder.segments, ExitCode.USAGE
        except Exception as e:  # noqa: BLE001 -- the server outlives a request
            # a bug in hsql, which a cold run answers with a crash report and
            # exit 70 -- so a served one does too, rather than with the code
            # for SQL the database rejected
            diagnostics.note(
                f"request failed: {redact_text(traceback.format_exc())}",
                stream=self._stderr,
            )
            diagnostics.report_crash(
                _crash_report(e, request.argv), stream=recorder.stderr()
            )
            return recorder.segments, ExitCode.CRASH
        return recorder.segments, code

    @contextlib.contextmanager
    def _as(self, request: protocol.Request, recorder: Recorder) -> Iterator[None]:
        """Make this process look like the client's, for one request.

        Its working directory, so a relative `-o`, `-f` or `--config-path`
        and the config files hsql discovers resolve where the caller is; the
        environment it forwards; and its streams, so `--color auto` reads the
        caller's terminal and `-f -` reads what it piped. All of it is
        process-wide, which is safe because requests run one at a time and
        nothing else here touches any of it.

        Raises: NotADirectoryError if the client's directory cannot be entered.
        """
        try:
            os.chdir(request.cwd)
        except OSError as e:
            raise NotADirectoryError(
                f"the working directory {request.cwd} cannot be entered: {e.strerror}"
            ) from e
        saved_environ = {
            key: os.environ.get(key) for key in protocol.FORWARDED_ENV_VARS
        }
        saved_streams = (sys.stdin, sys.stdout, sys.stderr)
        try:
            for key in protocol.FORWARDED_ENV_VARS:
                if key in request.environ:
                    os.environ[key] = request.environ[key]
                else:
                    os.environ.pop(key, None)
            sys.stdin = io.TextIOWrapper(
                io.BytesIO(request.stdin or b""), encoding="utf-8"
            )
            sys.stdout = recorder.stdout()
            sys.stderr = recorder.stderr()
            yield
        finally:
            with contextlib.suppress(Exception):
                sys.stdout.flush()
                sys.stderr.flush()
            sys.stdin, sys.stdout, sys.stderr = saved_streams
            for key, value in saved_environ.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            os.chdir(self._cwd)

    def _answer(
        self,
        connection: socket.socket,
        segments: Sequence[tuple[int, bytearray]],
        code: int,
    ) -> None:
        """Send the recorded streams, chunked, and then the exit code."""
        try:
            for kind, data in segments:
                for start in range(0, len(data), protocol.CHUNK_SIZE):
                    protocol.send_frame(
                        connection,
                        kind,
                        bytes(data[start : start + protocol.CHUNK_SIZE]),
                    )
            protocol.send_frame(connection, protocol.EXIT, bytes([code & 0xFF]))
        except OSError:
            # the client went away; the request ran, and there is nobody to
            # tell about it
            diagnostics.report_client_gone(stream=self._stderr)


def _crash_report(error: BaseException, argv: "Sequence[str]") -> "Path | None":
    """Write a crash report for a bug hit while serving, or None if it could not.

    The same report a cold run writes, so a caller who hit the bug through a
    session has the same file to attach.
    """
    from harlequin.crash import build_crash_report, write_crash_report
    from harlequin.redact import redact_conn_str

    try:
        context = {
            "argv": " ".join(redact_conn_str(list(argv))),
            "session": True,
        }
        return write_crash_report(build_crash_report(error, context, program=PROGRAM))
    except BaseException:
        return None


def _warm_imports() -> None:
    """Pay for the execution core before the first request, not during it.

    The cold path defers these so that an invocation that never runs SQL never
    loads them; a server exists to run SQL, and the seconds they cost belong
    to its start-up rather than to whichever caller happens to be first.
    """
    import harlequin.export  # noqa: F401
    import harlequin.hsql.output  # noqa: F401
    import harlequin.layout  # noqa: F401
    import harlequin.query  # noqa: F401
    import harlequin.statements  # noqa: F401


def peer_uid(connection: socket.socket) -> int | None:
    """The uid of the process at the other end, or None where the kernel cannot say."""
    try:
        if hasattr(socket, "SO_PEERCRED"):
            # struct ucred: pid, uid, gid
            credentials = connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
            return int(struct.unpack("3i", credentials)[1])
        if hasattr(socket, "LOCAL_PEERCRED"):
            # struct xucred: version, uid, then the groups. SOL_LOCAL is 0.
            credentials = connection.getsockopt(0, socket.LOCAL_PEERCRED, 76)
            return int(struct.unpack_from("II", credentials)[1])
    except (OSError, struct.error):
        return None
    return None


@contextlib.contextmanager
def _in_directory(path: str) -> Iterator[None]:
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
