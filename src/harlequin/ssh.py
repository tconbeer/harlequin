"""An `ssh` child process holding local forwards open, for either command.

Harlequin runs `ssh` and touches nothing else. The connection details already
name the local end of a forward -- `host = "localhost"`, `port = 15439` -- so no
adapter is told a tunnel exists. What lives here is the child's lifetime: build
the argv, ask `ssh -G` what it is about to forward, wait for those ports to
answer, and kill it on the way out.

Nothing a user typed is parsed: `ssh` owns the syntax of a destination and of a
forward spec, so both values reach it verbatim and its stderr is what an error
quotes. They are checked for the characters ssh would read as an option or
expand into a shell (`_refuse_unsafe_value()`), because a config file is
discovered in the working directory. The one thing this module reads is `ssh
-G`'s own output (`parse_config()`), which says which local port to wait on: a
forward declared in `~/.ssh/config` never reaches Harlequin as a port number.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Collection, Sequence

from harlequin.exception import HarlequinSshError
from harlequin.redact import redact_text

SSH = "ssh"
"""The client, found on PATH."""

DEFAULT_TIMEOUT = 60.0
"""Seconds to wait for the forwards, when nothing says otherwise.

Long because the wait is on a human as often as on a network: an ssh that opens
a browser for an identity provider, or asks for a hardware key, is a minute of
someone reading a screen. `--ssh-batch-mode` is what an unattended caller passes
to fail at the first prompt instead of waiting this out.
"""

ERROR_TITLE = "Harlequin could not open the SSH tunnel."

_POLL_INTERVAL = 0.05
_CONNECT_TIMEOUT = 0.5
"""How long one probe of one local port may take."""

_GRACE_SECONDS = 1.0
"""What a tunnel with no port to poll waits, before trusting the child."""

_TERMINATE_SECONDS = 2.0
"""How long a terminated child has to exit before it is killed."""

_STDERR_SETTLE_SECONDS = 0.5
"""How long a caller quoting ssh waits for the drain to catch up.

Bounded rather than open-ended: the pipe reaches EOF only when every write end
is closed, and a `ProxyJump` helper or an askpass inherits ssh's stderr and
holds one open after ssh itself is gone.
"""

_STDERR_LIMIT = 8192
"""Bytes of ssh's stderr kept for an error to quote."""

_ANY_INTERFACE = {"", "*", "0.0.0.0", "::"}
"""Bind addresses that are not themselves an address to connect to."""

_KEYWORD = re.compile(r"^([a-z][a-z0-9]*)(?:\s+(.*))?$")

_UNSAFE_IN_VALUE = re.compile(r"""[\x00-\x20\x7f`$\\"'|&;<>(){}]""")
"""Characters no destination or forward spec has, and that ssh may expand.

A destination reaches the user's own `ssh_config` as `%h`, `%r` and `%p`, which
a `ProxyCommand` there runs through a shell (CVE-2023-51385, CVE-2025-61984).
Since a config file is discovered in the working directory, the value can come
from a cloned repository's `pyproject.toml` rather than from its user.
"""


@dataclass(frozen=True)
class Forward:
    """One local forward, as `ssh -G` printed it: what listens, where it goes.

    Two whitespace-separated fields, each a port, a `[host]:port`, or a socket
    path. Only the listening side is ever read for a number.
    """

    listen: str
    destination: str

    @property
    def endpoint(self) -> tuple[str, int] | None:
        """Where to connect to test this forward; None for a unix socket."""
        return _endpoint(self.listen)

    def __str__(self) -> str:
        return f"{_pretty(self.listen)} -> {_pretty(self.destination)}"


@dataclass(frozen=True)
class SshConfig:
    """What `ssh -G` resolved for the argv we are about to run.

    One list whether a forward came from `--ssh-forward`, a `Host` block, or
    both: `-G` echoes the command line's `-L` flags back along with the config
    file's.
    """

    forwards: tuple[Forward, ...]


def build_argv(
    host: str,
    forwards: Sequence[str] = (),
    *,
    batch_mode: bool = False,
) -> list[str]:
    """The `ssh` command that holds `forwards` open, and runs nothing.

    `ExitOnForwardFailure` is the only option Harlequin imposes on its own: a
    forward that silently did not happen is the one failure a user cannot
    diagnose.

    Raises: HarlequinSshError for a value ssh would read as an option or expand
    into a shell.
    """
    _refuse_unsafe_value("--ssh-host", host)
    for forward in forwards:
        _refuse_unsafe_value("--ssh-forward", forward)
    argv = [SSH, "-N", "-o", "ExitOnForwardFailure=yes"]
    if batch_mode:
        argv += ["-o", "BatchMode=yes"]
    for forward in forwards:
        argv += ["-L", forward]
    argv.append(host)
    return argv


def build_tunnel(
    host: str,
    forwards: Sequence[str] = (),
    *,
    batch_mode: bool = False,
    allow_reuse: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> SshTunnel:
    """The tunnel this invocation runs, having asked ssh what it will forward.

    Raises: HarlequinSshError if nothing anywhere configures a local forward,
    which is a destination Harlequin would connect to and never use.
    """
    argv = build_argv(host, forwards, batch_mode=batch_mode)
    config = resolve_config(argv, timeout=timeout)
    if config is not None and not config.forwards:
        raise HarlequinSshError(
            f"{host} configures no local forward, so a tunnel to it would "
            "carry nothing. Pass --ssh-forward LOCAL:HOST:REMOTE, or add a "
            f"LocalForward line to the {host} block of your ssh config.",
            title=ERROR_TITLE,
        )
    return SshTunnel(
        argv,
        forwards=config.forwards if config is not None else (),
        host=host,
        allow_reuse=allow_reuse,
        timeout=timeout,
    )


def resolve_config(argv: Sequence[str], *, timeout: float) -> SshConfig | None:
    """What `ssh -G` says the argv about to run resolves to, or None.

    None is the degraded answer -- no client, no `-G`, or output this cannot
    read -- and every caller reads it as "ask ssh nothing else": no poll and no
    forwards-nothing error.
    """
    probe = [_client_path(argv[0]), "-G", *argv[1:]]
    try:
        completed = subprocess.run(
            probe,
            capture_output=True,
            timeout=timeout,
            # a config that runs `Match exec` inherits our stdin otherwise
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return parse_config(completed.stdout.decode("utf-8", errors="replace"))


def parse_config(text: str) -> SshConfig | None:
    """`ssh -G`'s output, or None if this is not `ssh -G`'s output.

    Each line is a lowercase keyword and its resolved value, and the only one
    read is `localforward`. A `hostname` line is what tells the two apart: every
    `-G` prints one, so text without it is something else, and the caller
    degrades rather than concluding there is nothing to forward.
    """
    forwards: list[Forward] = []
    is_ssh_output = False
    for line in text.splitlines():
        match = _KEYWORD.match(line.strip())
        if match is None:
            continue
        keyword, value = match.group(1), (match.group(2) or "").strip()
        if keyword == "hostname":
            is_ssh_output = True
        elif keyword == "localforward":
            fields = value.split()
            if len(fields) == 2:
                forwards.append(Forward(listen=fields[0], destination=fields[1]))
    if not is_ssh_output:
        return None
    return SshConfig(forwards=tuple(forwards))


class SshTunnel:
    """An `ssh` child process holding one or more local forwards open.

    It inherits the terminal, so a passphrase or 2FA prompt still reaches a
    human, and it dies with the process that started it.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        forwards: Sequence[Forward] = (),
        host: str = "",
        allow_reuse: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.argv = list(argv)
        self.forwards = tuple(forwards)
        self.host = host
        self.allow_reuse = allow_reuse
        self.timeout = timeout
        self.reused = False
        """Whether a listener that was already there is what answers now."""
        self.echo_stderr = True
        """Whether ssh's stderr goes to ours while the tunnel opens.

        True while a caller still owns the screen, which is where a helper's
        "to authenticate, visit ..." has to appear to be worth anything.
        """
        self._process: subprocess.Popen[bytes] | None = None
        self._stderr: _Stderr | None = None

    @property
    def endpoints(self) -> tuple[tuple[str, int], ...]:
        """Every local address a caller can wait on. Socket paths are not polled."""
        return tuple(
            endpoint
            for forward in self.forwards
            if (endpoint := forward.endpoint) is not None
        )

    @property
    def running(self) -> bool:
        """Whether the child is still holding the forwards open."""
        return self._process is not None and self._process.poll() is None

    def describe(self) -> str:
        """The one line that says which database is behind the local port."""
        listed = ", ".join(str(forward) for forward in self.forwards)
        via = f" via {self.host}" if self.host else ""
        return f"{listed}{via}" if listed else f"tunnel to {self.host}"

    def start(self) -> None:
        """Start it and block until the forwarded ports accept connections.

        Raises: HarlequinSshError, quoting ssh's stderr.
        """
        if self.running:
            return
        self.reused = False
        already_bound = frozenset(
            endpoint for endpoint in self.endpoints if _accepts(endpoint)
        )
        argv = [_client_path(self.argv[0]), *self.argv[1:]]
        try:
            process = subprocess.Popen(
                argv,
                # ssh's prompts go to the terminal it inherits, not to this
                # pipe, so capturing stderr does not swallow them
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            raise HarlequinSshError(
                f"Harlequin could not run {argv[0]}: {e}", title=ERROR_TITLE
            ) from e
        self._process = process
        self._stderr = _Stderr(process.stderr, echo=self.echo_stderr)
        atexit.register(self.stop)
        try:
            self._wait_until_ready(already_bound=already_bound)
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        """Kill the child, if there is one. Safe to call more than once."""
        atexit.unregister(self.stop)
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_TERMINATE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=_TERMINATE_SECONDS)
        if self._stderr is not None:
            self._stderr.close()

    def __enter__(self) -> SshTunnel:
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def _wait_until_ready(self, *, already_bound: Collection[tuple[str, int]]) -> None:
        """Block until every forwarded port answers, or say why it never did.

        A port that answered before the child started proves nothing by
        answering now: ssh binds its local forwards only after authenticating,
        so a listener already on the port satisfies the probe for the whole
        handshake. Where one did, the only outcomes are the child exiting --
        which `ExitOnForwardFailure=yes` guarantees it does when it cannot take
        the port -- and the deadline.
        """
        assert self._process is not None
        deadline = time.monotonic() + self.timeout
        if not self.endpoints:
            # no port to wait on: give the child a moment to fail outright
            time.sleep(max(0.0, min(_GRACE_SECONDS, self.timeout)))
            if self._process.poll() is not None:
                raise self._child_failed()
            return
        while True:
            if self._process.poll() is not None:
                # ports that answer after ssh exited are someone else's tunnel
                if already_bound and self.allow_reuse and self._answering():
                    self.reused = True
                    return
                raise self._child_failed()
            # a port that was bound before the child started is not polled
            # again while it runs: its answer would say nothing, and a listener
            # whose owner never accepts has a backlog to fill
            if not already_bound and self._answering():
                return
            if time.monotonic() >= deadline:
                raise self._timed_out(already_bound=already_bound)
            time.sleep(_POLL_INTERVAL)

    def _answering(self) -> bool:
        """Whether every forwarded port accepts a connection right now."""
        return all(_accepts(endpoint) for endpoint in self.endpoints)

    def _child_failed(self) -> HarlequinSshError:
        assert self._process is not None
        # the child has exited, but what it wrote on the way out may still be
        # in the pipe -- and it is the whole of why this failed
        if self._stderr is not None:
            self._stderr.settle()
        return HarlequinSshError(
            f"ssh exited with code {self._process.returncode} without opening "
            f"the forward.\n\n{self._unseen()}".rstrip(),
            title=ERROR_TITLE,
        )

    def _timed_out(
        self, *, already_bound: Collection[tuple[str, int]] = ()
    ) -> HarlequinSshError:
        if self._stderr is not None:
            # a helper's last line may have no newline of its own
            self._stderr.flush()
        if already_bound:
            listed = ", ".join(f"{host}:{port}" for host, port in sorted(already_bound))
            reason = (
                f"{listed} was already bound when the tunnel started, and ssh "
                f"has not exited within {self.timeout:g}s to say whether it "
                "could take the port. Free it, forward a different local port, "
                "or pass --ssh-allow-reuse to connect through the listener "
                "that already has it."
            )
        else:
            reason = (
                f"ssh did not open the forward within {self.timeout:g}s. It is "
                "most likely waiting for a passphrase, a password, or "
                "confirmation of a host key; answer it, or pass "
                "--ssh-batch-mode to fail immediately instead of waiting."
            )
        return HarlequinSshError(
            f"{reason}\n\n{self._unseen()}".rstrip(), title=ERROR_TITLE
        )

    def _unseen(self) -> str:
        """What ssh said that the user has not already read on stderr.

        Nothing, where it was echoed as it arrived: an error that repeated it
        would print a helper's instructions twice.
        """
        if self._stderr is None or self.echo_stderr:
            return ""
        return self._stderr.text()


class _Stderr:
    """What ssh wrote to stderr: passed through as it arrives, and kept.

    Passed through because the thing a user most needs to see arrives while the
    tunnel is still opening and nothing has failed yet -- Tailscale's "to
    authenticate, visit ..." is written here, and a helper's instructions are
    worthless after the timeout they caused. `echo=False` is for a caller that
    no longer owns the screen; it keeps the text for an error to quote instead.

    A line at a time, so `redact_text()` sees whole lines: ssh is third-party
    output, and a driver's DSN can reach it.
    """

    def __init__(self, stream: Any, *, echo: bool = True) -> None:
        self._stream = stream
        self._echo = echo
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._pending = b""
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def text(self) -> str:
        return b"".join(self._chunks).decode("utf-8", errors="replace").strip()

    def settle(self) -> None:
        """Wait for the drain to catch up with a child that has exited.

        `Popen.wait()` returns before the pipe the child wrote to has been read
        to the end, so a caller that quotes ssh calls this first, or quotes
        nothing.
        """
        self._thread.join(timeout=_STDERR_SETTLE_SECONDS)

    def flush(self) -> None:
        """Show a last line ssh left without a newline, before giving up on it."""
        pending, self._pending = self._pending, b""
        if pending and self._echo:
            self._show(pending + b"\n")

    def close(self) -> None:
        self._thread.join(timeout=_STDERR_SETTLE_SECONDS)
        with contextlib.suppress(OSError):
            self._stream.close()

    def _drain(self) -> None:
        # `read1()`, not `read()`: the latter blocks until it has the full 1024
        # bytes or the pipe closes, and every ssh diagnostic is shorter -- so a
        # helper's instructions would not appear until the tunnel gave up.
        with contextlib.suppress(OSError, ValueError):
            while chunk := self._stream.read1(1024):
                self._keep(chunk)
                if self._echo:
                    self._pending += chunk
                    lines, _, self._pending = self._pending.rpartition(b"\n")
                    if lines:
                        self._show(lines + b"\n")
        self.flush()

    def _keep(self, chunk: bytes) -> None:
        self._chunks.append(chunk)
        self._size += len(chunk)
        while self._size > _STDERR_LIMIT and len(self._chunks) > 1:
            self._size -= len(self._chunks.popleft())

    def _show(self, raw: bytes) -> None:
        text = redact_text(raw.decode("utf-8", errors="replace"))
        with contextlib.suppress(OSError, ValueError):
            sys.stderr.write(text)
            sys.stderr.flush()


def _refuse_unsafe_value(option: str, value: str) -> None:
    """Refuse a value ssh would read as an option, or expand into a shell.

    `ssh` has no `--`, so a value starting with `-` reaches it as a flag; and a
    destination reaches the user's own config as `%h`, `%r` and `%p`, where a
    `ProxyCommand` runs it through a shell. Both matter because a config file
    is discovered in the working directory: neither value is necessarily one
    its user typed.
    """
    if value.startswith("-"):
        raise HarlequinSshError(
            f"{option}={value!r} starts with a dash, which ssh would read as an "
            "option rather than a value. Options belong in your ssh config.",
            title=ERROR_TITLE,
        )
    found = _UNSAFE_IN_VALUE.search(value)
    if found is not None:
        raise HarlequinSshError(
            f"{option}={value!r} contains {found.group()!r}, which a "
            "ProxyCommand in your ssh config would expand into a shell. "
            "A destination and a forward spec have no use for it.",
            title=ERROR_TITLE,
        )


def _client_path(program: str) -> str:
    """`program` as a path, resolved against PATH and nothing else.

    Windows' `CreateProcess` searches the working directory ahead of PATH, so a
    repository that ships an `ssh.exe` would otherwise run in place of the
    user's client. A program that already names a path is returned as it is,
    and so is one PATH does not have -- letting the spawn raise as usual.
    """
    if os.path.dirname(program):
        return program
    suffixes = (
        os.environ.get("PATHEXT", ".EXE").split(os.pathsep)
        if os.name == "nt"
        else ("",)
    )
    for directory in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not directory:
            continue
        for suffix in suffixes:
            candidate = os.path.join(directory, program + suffix)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return program


def _split(field: str) -> tuple[str, str] | None:
    """A `ssh -G` address field as the host and port it names, or None.

    None for a unix socket, which counts as a forward and cannot be polled, and
    for anything else without a port number in it.
    """
    if "/" in field:
        return None
    if field.startswith("["):
        host, _, port = field[1:].partition("]:")
    elif ":" in field:
        host, _, port = field.rpartition(":")
    else:
        host, port = "", field
    return (host, port) if port.isdigit() else None


def _endpoint(field: str) -> tuple[str, int] | None:
    """A `ssh -G` listen field as an address to connect to, or None.

    A forward bound to every interface, or to no named one, is polled as
    `localhost` rather than as a literal address: that is the name ssh binds it
    under, and connecting by name tries every family it resolves to -- which
    `127.0.0.1` would not, on a host where `localhost` is IPv6 only.
    """
    split = _split(field)
    if split is None:
        return None
    host, port = split
    return ("localhost" if host in _ANY_INTERFACE else host), int(port)


def _pretty(field: str) -> str:
    """One `ssh -G` field, as the address it names.

    The bind address ssh reported, rather than the one `_endpoint()` polls: a
    forward on `0.0.0.0` is reachable from the whole network, and a notice that
    called it `localhost` would hide the thing worth noticing.
    """
    split = _split(field)
    if split is None:
        return field
    host, port = split
    host = host or "localhost"
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _accepts(endpoint: tuple[str, int]) -> bool:
    """Whether something is listening on a local port, right now."""
    try:
        with socket.create_connection(endpoint, timeout=_CONNECT_TIMEOUT):
            return True
    except OSError:
        return False
