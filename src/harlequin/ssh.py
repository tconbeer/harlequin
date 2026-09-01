"""An `ssh` child process holding local forwards open, for either command.

Harlequin runs `ssh` and touches nothing else. The connection details already
name the local end of a forward -- `host = "localhost"`, `port = 15439` -- so no
adapter is told a tunnel exists, and every adapter works, including the ones
nobody in this org maintains. What lives here is the child's lifetime: build the
argv, ask `ssh -G` what it is about to forward, wait for those ports to answer,
and kill it on the way out.

Nothing a user typed is parsed. `ssh` owns the syntax of a destination and of a
forward spec, and its error messages are better than any we would write, so both
values reach it verbatim and its stderr is what an error quotes. The one thing
this module reads is `ssh -G`'s own output (`parse_config()`), which says which
local port to wait on -- the motivating case puts the forward in `~/.ssh/config`,
where Harlequin never sees a port number at all.
"""

from __future__ import annotations

import atexit
import contextlib
import re
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from harlequin.config import CONFIG_ERROR_TITLE, DEFAULT_SSH_TIMEOUT, parse_seconds
from harlequin.exception import HarlequinConfigError, HarlequinSshError

SSH = "ssh"
"""The client, found on PATH: whichever one the user's `~/.ssh/config` is for."""

DEFAULT_TIMEOUT = DEFAULT_SSH_TIMEOUT
"""Seconds to wait for the forwards, when nothing says otherwise."""

ERROR_TITLE = "Harlequin could not open the SSH tunnel."

_POLL_INTERVAL = 0.05
_CONNECT_TIMEOUT = 0.5
"""How long one probe of one local port may take."""

_SETTLE_SECONDS = 1.0
"""How long a port that was already bound gets to prove it was ssh's to take.

Without this, a listener that answered before `ssh` even started would read as
the forward being up -- the one case where a port answering proves nothing.
"""

_GRACE_SECONDS = 1.0
"""What a tunnel with no port to poll waits instead, before trusting the child."""

_TERMINATE_SECONDS = 2.0
"""How long a terminated child has to exit before it is killed."""

_STDERR_LIMIT = 8192
"""Bytes of ssh's stderr kept for an error to quote."""

_LOOPBACK = "127.0.0.1"
_WILDCARD_HOSTS = {"", "*", "0.0.0.0", "localhost"}
_KEYWORD = re.compile(r"^([a-z][a-z0-9]*)(?:\s+(.*))?$")

_LIVE: set[SshTunnel] = set()
"""Every tunnel with a child running, for `stop_all()`."""


@dataclass(frozen=True)
class Forward:
    """One local forward, as `ssh -G` printed it: what listens, where it goes.

    Two whitespace-separated fields, each a port, a `[host]:port`, or a socket
    path. Kept as text because that is all a notice needs; only the listening
    side is ever read for a number.
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
    file's, so there are never two sources to keep in agreement.
    """

    forwards: tuple[Forward, ...]


@dataclass(frozen=True)
class SshOptions:
    """The five `--ssh-*` values, as either command's merged config supplies them."""

    host: str | None = None
    forwards: tuple[str, ...] = ()
    batch_mode: bool = False
    allow_reuse: bool = False
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> SshOptions:
        """What `config.take_ssh_keys()` took, as values `ssh` can be run with.

        Raises: HarlequinConfigError for a value its key does not take. A
        profile can put anything under one, and click has already vetted
        whatever was typed on the command line.
        """
        forwards = config.get("ssh_forward") or ()
        if isinstance(forwards, str):
            forwards = (forwards,)
        return cls(
            host=_text(config.get("ssh_host"), key="ssh_host"),
            forwards=tuple(
                _text(forward, key="ssh_forward") or "" for forward in forwards
            ),
            batch_mode=bool(config.get("ssh_batch_mode")),
            allow_reuse=bool(config.get("ssh_allow_reuse")),
            timeout=parse_seconds(config.get("ssh_timeout"), key="ssh_timeout")
            or DEFAULT_TIMEOUT,
        )


def open_tunnel(config: Mapping[str, Any]) -> SshTunnel | None:
    """The tunnel this invocation runs under, started, or None for no tunnel.

    Both commands' one line of this feature: everything from the five keys to a
    child process holding a forward open, with prompts still reaching a human
    because neither has taken the terminal yet.

    Raises: HarlequinConfigError for a value a key does not take, and
    HarlequinSshError for a tunnel that would not open.
    """
    options = SshOptions.from_config(config)
    if options.host is None:
        return None
    tunnel = build_tunnel(
        options.host,
        options.forwards,
        batch_mode=options.batch_mode,
        allow_reuse=options.allow_reuse,
        timeout=options.timeout,
    )
    tunnel.start()
    return tunnel


def stop_all() -> None:
    """Kill every tunnel this process started.

    The `atexit` backstop cannot reach a process that ends in `os._exit()`, and
    an ssh child that outlives the session is the thing this feature removes.
    """
    for tunnel in list(_LIVE):
        tunnel.stop()


def build_argv(
    host: str,
    forwards: Sequence[str] = (),
    *,
    batch_mode: bool = False,
) -> list[str]:
    """The `ssh` command that holds `forwards` open, and runs nothing.

    `ExitOnForwardFailure` is the only option Harlequin imposes on its own: a
    forward that silently did not happen is the one failure a user cannot
    diagnose. Notably not imposed is `ServerAliveInterval`, which a command-line
    `-o` would take away from a `Host` block that already sets it.

    Raises: HarlequinSshError if a value would reach `ssh` as a flag.
    """
    _refuse_a_flag("--ssh-host", host)
    for forward in forwards:
        _refuse_a_flag("--ssh-forward", forward)
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

    Raises: HarlequinConfigError if nothing anywhere configures a local
    forward, which is a destination Harlequin would connect to and never use,
    and HarlequinSshError for an argv `ssh` must not be handed.
    """
    argv = build_argv(host, forwards, batch_mode=batch_mode)
    config = resolve_config(argv, timeout=timeout)
    if config is not None and not config.forwards:
        # a usage error rather than a failure to connect: nothing has been run
        # yet, and what is wrong is what was asked for
        raise HarlequinConfigError(
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
    read -- and every caller reads it as "ask ssh nothing else": no poll, no
    forwards-nothing error, and the adapter's own connection is the test.
    """
    probe = [argv[0], "-G", *argv[1:]]
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

    A child rather than `ssh -f`: the process that survives a fork is not ours
    to kill, which is the orphaned tunnel this feature exists to remove. It also
    keeps the terminal, so a passphrase or 2FA prompt still reaches a human --
    which is why both commands start it before anything owns the screen.
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

    def notice(self) -> str:
        """What a started tunnel says for itself, on stderr or as a notification.

        It is what tells a user which database they are actually looking at --
        or, having reused a listener, that the one answering is not one
        Harlequin opened.
        """
        if self.reused:
            listed = ", ".join(f"{host}:{port}" for host, port in self.endpoints)
            return (
                f"{listed} is already bound; connecting through the existing "
                "listener (--ssh-allow-reuse)"
            )
        return self.describe()

    def cache_material(self) -> tuple[str, ...]:
        """What a connection reached through this tunnel is keyed by, beside itself.

        Two bastions fronting two databases both look like `localhost:15439`,
        and a catalog cache or a query history shared between them would be one
        database's answers under the other's name.
        """
        return (self.host, *(f"{f.listen} {f.destination}" for f in self.forwards))

    def start(self) -> None:
        """Start it and block until the forwarded ports accept connections.

        Raises: HarlequinSshError, quoting ssh's stderr.
        """
        if self.running:
            return
        self.reused = False
        already_bound = any(_accepts(endpoint) for endpoint in self.endpoints)
        try:
            process = subprocess.Popen(
                self.argv,
                # ssh's prompts go to the terminal it inherits, not to this
                # pipe, so capturing stderr costs no interactivity and buys an
                # error message written by the program that failed.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            raise HarlequinSshError(
                f"Harlequin could not run {self.argv[0]}: {e}", title=ERROR_TITLE
            ) from e
        self._process = process
        self._stderr = _Stderr(process.stderr)
        _LIVE.add(self)
        atexit.register(self.stop)
        try:
            self._wait_until_ready(already_bound=already_bound)
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        """Kill the child, if there is one. Safe to call more than once."""
        atexit.unregister(self.stop)
        _LIVE.discard(self)
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

    def _wait_until_ready(self, *, already_bound: bool) -> None:
        """Block until every forwarded port answers, or say why it never did."""
        assert self._process is not None
        started = time.monotonic()
        deadline = started + self.timeout
        settled = started + min(_SETTLE_SECONDS, self.timeout)
        if not self.endpoints:
            # degraded, or forwarding to a socket path: there is no port to wait
            # on, so the child gets a moment to fail outright and the adapter's
            # connection is the real test.
            time.sleep(min(_GRACE_SECONDS, self.timeout))
            if self._process.poll() is not None:
                raise self._child_failed()
            return
        while True:
            exited = self._process.poll() is not None
            answering = all(_accepts(endpoint) for endpoint in self.endpoints)
            if exited:
                # `ExitOnForwardFailure=yes`, so ssh exits rather than connect
                # without the forward. Ports that answer anyway are someone
                # else's tunnel, which is a thing to use only when asked.
                if answering and already_bound and self.allow_reuse:
                    self.reused = True
                    return
                raise self._child_failed()
            if answering and (not already_bound or time.monotonic() >= settled):
                return
            if time.monotonic() >= deadline:
                raise self._timed_out()
            time.sleep(_POLL_INTERVAL)

    def _child_failed(self) -> HarlequinSshError:
        assert self._process is not None
        detail = self._stderr.text() if self._stderr is not None else ""
        return HarlequinSshError(
            f"ssh exited with code {self._process.returncode} without opening "
            f"the forward.\n\n{detail}".rstrip(),
            title=ERROR_TITLE,
        )

    def _timed_out(self) -> HarlequinSshError:
        detail = self._stderr.text() if self._stderr is not None else ""
        return HarlequinSshError(
            f"ssh did not open the forward within {self.timeout:g}s. It is most "
            "likely waiting for a passphrase, a password, or confirmation of a "
            "host key; answer it, or pass --ssh-batch-mode to fail immediately "
            f"instead of waiting.\n\n{detail}".rstrip(),
            title=ERROR_TITLE,
        )


class _Stderr:
    """The tail of what ssh wrote to stderr, drained so it cannot block on it."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def text(self) -> str:
        return b"".join(self._chunks).decode("utf-8", errors="replace").strip()

    def close(self) -> None:
        self._thread.join(timeout=_TERMINATE_SECONDS)
        with contextlib.suppress(OSError):
            self._stream.close()

    def _drain(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            while chunk := self._stream.read(1024):
                self._chunks.append(chunk)
                self._size += len(chunk)
                while self._size > _STDERR_LIMIT and len(self._chunks) > 1:
                    self._size -= len(self._chunks.popleft())


def _text(value: Any, *, key: str) -> str | None:
    """One config value as the string `ssh` takes, or None where nothing was set."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise HarlequinConfigError(
            f"{key}={value!r} is not text that ssh could read.",
            title=CONFIG_ERROR_TITLE,
        )
    return value


def _refuse_a_flag(option: str, value: str) -> None:
    """Refuse a value `ssh` would read as an option rather than as a value.

    `ssh` has no `--`, so a destination or a forward spec starting with `-`
    reaches it as a flag -- and a config file is discovered in the working
    directory, where a cloned repo must not be able to smuggle in an
    `-oProxyCommand=`.
    """
    if value.startswith("-"):
        raise HarlequinSshError(
            f"{option}={value!r} starts with a dash, which ssh would read as an "
            "option rather than a value. Options belong in your ssh config.",
            title=ERROR_TITLE,
        )


def _endpoint(field: str) -> tuple[str, int] | None:
    """A `ssh -G` listen field as an address to connect to, or None.

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
    if not port.isdigit():
        return None
    if host in _WILDCARD_HOSTS:
        # a forward bound to every interface is reachable on the loopback, which
        # is the interface the adapter is pointed at
        host = _LOOPBACK
    elif host == "::":
        host = "::1"
    return host, int(port)


def _pretty(field: str) -> str:
    """One `ssh -G` field, as a person would write the address it names."""
    endpoint = _endpoint(field)
    if endpoint is None:
        return field
    host, port = endpoint
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _accepts(endpoint: tuple[str, int]) -> bool:
    """Whether something is listening on a local port, right now."""
    try:
        with socket.create_connection(endpoint, timeout=_CONNECT_TIMEOUT):
            return True
    except OSError:
        return False
