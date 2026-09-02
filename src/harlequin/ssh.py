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
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Collection, Iterator, Mapping, Sequence

from harlequin.config import CONFIG_ERROR_TITLE, DEFAULT_SSH_TIMEOUT, parse_seconds
from harlequin.exception import HarlequinConfigError, HarlequinSshError
from harlequin.redact import redact_text

SSH = "ssh"
"""The client, found on PATH."""

DEFAULT_TIMEOUT = DEFAULT_SSH_TIMEOUT
"""Seconds to wait for the forwards, when nothing says otherwise."""

ERROR_TITLE = "Harlequin could not open the SSH tunnel."

KEEPALIVE_SECONDS = 30
"""What Harlequin sets `ServerAliveInterval` to where `ssh -G` resolves it to 0.

`ssh` sends no keepalives by default, so an idle forward behind a NAT or a
firewall is reaped silently.

A resolved `0` always becomes this, including where a `Host` block set it to
`0` deliberately: `ssh -G` prints `serveraliveinterval 0` for both "nothing set
it" and "something set it to zero", so the two cannot be told apart. Any other
resolved value is left as it is.
"""

KEEPALIVE_COUNT = 3
"""How many unanswered keepalives end a connection Harlequin set the interval on.

Imposed with the interval rather than inherited, so the budget is a known
`KEEPALIVE_SECONDS * KEEPALIVE_COUNT`. `ServerAliveCountMax 0` is inert while
the interval is 0 and would otherwise end a healthy connection at the first
keepalive.
"""

_POLL_INTERVAL = 0.05
_CONNECT_TIMEOUT = 0.5
"""How long one probe of one local port may take."""

_GRACE_SECONDS = 1.0
"""What a tunnel with no port to poll waits, before trusting the child."""

_PROBE_SECONDS = 10.0
"""How long `ssh -G` has to print the resolved config.

Its own bound rather than the caller's: `-G` connects to nothing, so the wait a
user configures -- for a network, or for a person answering a prompt -- says
nothing about it, and a small one would otherwise skip the readiness poll by
leaving Harlequin with no forwards to wait on.
"""

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

_CONTROL = re.compile(r"[^\n\t\x20-\x7e\xa0-\U0010ffff]")
"""Every character a terminal reads as a command rather than as text."""

_LIVE: set[SshTunnel] = set()
"""Every tunnel with a child running, for `stop_all()`."""

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

    server_alive_interval: int | None = None
    """What the resolved config sets. Real `ssh -G` output always says.

    None is for text that named no `serveraliveinterval` at all, which leaves
    the keepalive alone rather than guessing at one.
    """


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
        return cls(
            host=_text(config.get("ssh_host"), key="ssh_host"),
            forwards=_forwards(config.get("ssh_forward")),
            batch_mode=_flag(config.get("ssh_batch_mode"), key="ssh_batch_mode"),
            allow_reuse=_flag(config.get("ssh_allow_reuse"), key="ssh_allow_reuse"),
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


@contextlib.contextmanager
def stopping_on_signal() -> Iterator[None]:
    """Stop every tunnel on `SIGTERM` and `SIGHUP`, for as long as one is up.

    Neither signal runs an `atexit` handler, so a session that is killed, or
    whose terminal goes away, would otherwise leave `ssh` holding a forward
    open.

    Having stopped them, the handler dies of the signal it caught rather than
    raising: a `SystemExit` here unwinds the main thread while a worker is
    still inside a database driver, which aborts the interpreter and exits 134.

    A no-op off the main thread, and on a platform without the signal.
    """
    replaced: list[tuple[int, Any]] = []

    def handle(number: int, frame: Any) -> None:
        stop_all()
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()
        signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)

    for name in ("SIGTERM", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            replaced.append((number, signal.signal(number, handle)))
    try:
        yield
    finally:
        for number, previous in replaced:
            if previous is None:
                # not installed from Python, and `signal()` takes no None back
                continue
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(number, previous)


def build_argv(
    host: str,
    forwards: Sequence[str] = (),
    *,
    batch_mode: bool = False,
    keepalive: int | None = None,
) -> list[str]:
    """The `ssh` command that holds `forwards` open, and runs nothing.

    `ExitOnForwardFailure` is the only option Harlequin imposes unconditionally:
    a forward that silently did not happen is the one failure a user cannot
    diagnose. `keepalive` is passed where the resolved config resolves the
    interval to zero, and carries its own `ServerAliveCountMax` so the budget
    it opens is a known one.

    Raises: HarlequinSshError for a value ssh would read as an option or expand
    into a shell.
    """
    _refuse_unsafe_value("--ssh-host", host)
    for forward in forwards:
        _refuse_unsafe_value("--ssh-forward", forward)
    argv = [SSH, "-N", "-o", "ExitOnForwardFailure=yes"]
    if batch_mode:
        argv += ["-o", "BatchMode=yes"]
    if keepalive is not None:
        argv += [
            "-o",
            f"ServerAliveInterval={keepalive}",
            "-o",
            f"ServerAliveCountMax={KEEPALIVE_COUNT}",
        ]
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
    and HarlequinSshError for an argv `ssh` must not be handed, or would not
    take.
    """
    argv = build_argv(host, forwards, batch_mode=batch_mode)
    config = resolve_config(argv)
    if config is not None and config.server_alive_interval == 0:
        # nothing in the resolved config keeps this connection alive; the added
        # option changes nothing else `-G` would report
        argv = build_argv(
            host, forwards, batch_mode=batch_mode, keepalive=KEEPALIVE_SECONDS
        )
    if config is not None and not config.forwards:
        raise HarlequinConfigError(
            f"{host} configures no local forward, so a tunnel to it would "
            "carry nothing. Pass --ssh-forward LOCAL:HOST:REMOTE, or add a "
            f"LocalForward line to the {host} block of your ssh config.",
            title=ERROR_TITLE,
        )
    return SshTunnel(
        argv,
        forwards=config.forwards if config is not None else (),
        requested=tuple(forwards),
        resolved=config is not None,
        host=host,
        allow_reuse=allow_reuse,
        timeout=timeout,
    )


def resolve_config(
    argv: Sequence[str], *, timeout: float = _PROBE_SECONDS
) -> SshConfig | None:
    """What `ssh -G` says the argv about to run resolves to, or None.

    None is the degraded answer -- no client, no `-G`, or output this cannot
    read -- and every caller reads it as "ask ssh nothing else": no poll and no
    forwards-nothing error.

    Raises: HarlequinSshError where the probe ran and rejected the argv. That
    is the same argv `start()` is about to run, so the run ends either way, and
    ssh names the value that is wrong while nothing has been spawned.
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
        said = _readable(completed.stderr.decode("utf-8", errors="replace")).strip()
        raise HarlequinSshError(
            f"ssh would not accept the tunnel's options.\n\n{said}".rstrip(),
            title=ERROR_TITLE,
        )
    return parse_config(completed.stdout.decode("utf-8", errors="replace"))


def parse_config(text: str) -> SshConfig | None:
    """`ssh -G`'s output, or None if this is not `ssh -G`'s output.

    Each line is a lowercase keyword and its resolved value; the two read are
    `localforward` and `serveraliveinterval`. A `hostname` line is what tells
    `-G`'s output from anything else that reached this: every
    `-G` prints one, so text without it is something else, and the caller
    degrades rather than concluding there is nothing to forward.
    """
    forwards: list[Forward] = []
    server_alive_interval: int | None = None
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
        elif keyword == "serveraliveinterval":
            with contextlib.suppress(ValueError):
                server_alive_interval = int(value)
    if not is_ssh_output:
        return None
    return SshConfig(
        forwards=tuple(forwards), server_alive_interval=server_alive_interval
    )


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
        requested: Sequence[str] = (),
        resolved: bool = True,
        host: str = "",
        allow_reuse: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.argv = list(argv)
        self.forwards = tuple(forwards)
        self.requested = tuple(requested)
        """The forward specs a caller asked for, as `ssh -L` spells them."""
        self.resolved = resolved
        """Whether `ssh -G` said what this tunnel forwards.

        False leaves `forwards` empty however many were asked for, so there is
        nothing to poll and nothing to name: what the tunnel opened is taken on
        the child's word.
        """
        self.host = host
        self.allow_reuse = allow_reuse
        self.timeout = timeout
        self.reused = False
        """Whether a listener that was already there is what answers now."""
        self.dropped = False
        """Whether the child exited on its own, taking the forward with it."""
        self.restart_failed = False
        """Whether a restart already failed, and so must not be tried again."""
        self.echo_stderr = True
        """Whether ssh's stderr goes to ours while the tunnel opens.

        True while a caller still owns the screen, which is where a helper's
        "to authenticate, visit ..." has to appear to be worth anything.
        """
        self._process: subprocess.Popen[bytes] | None = None
        self._stderr: _Output | None = None
        self._stdout: _Output | None = None
        self._on_close: Callable[[str], None] | None = None
        self._restart_lock = threading.Lock()
        """Held across a restart, so two of them cannot overlap.

        The loser of the race would otherwise fail to bind the port the winner
        just took, and mark a working tunnel as never to be reopened again.
        """

    @property
    def endpoints(self) -> tuple[tuple[str, int], ...]:
        """Every local address a caller can wait on. Socket paths are not polled."""
        return tuple(
            endpoint
            for forward in self.forwards
            if (endpoint := forward.endpoint) is not None
        )

    @property
    def needs_restart(self) -> bool:
        """Whether the next thing that needs the database should reopen this first.

        False once a restart has failed: a background retry storm against a
        bastion that is down is how an account gets locked.
        """
        return self.dropped and not self.restart_failed

    @property
    def running(self) -> bool:
        """Whether the child is still holding the forwards open."""
        return self._process is not None and self._process.poll() is None

    def describe(self) -> str:
        """The one line that says which database is behind the local port."""
        listed = ", ".join(str(forward) for forward in self.forwards)
        via = f" via {self.host}" if self.host else ""
        return f"{listed}{via}" if listed else f"tunnel to {self.host}"

    @property
    def exposed(self) -> tuple[str, ...]:
        """Every forward of this tunnel that is not bound to loopback alone.

        A forward on `0.0.0.0` or `*` reaches the whole network the machine is
        on, which is worth saying out loud: the spec may come from a config
        file discovered in the working directory rather than from its user.
        """
        return tuple(
            _pretty(forward.listen)
            for forward in self.forwards
            if _is_exposed(forward.listen)
        )

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

    def warnings(self) -> tuple[str, ...]:
        """What is worth saying about this tunnel beyond where it goes.

        A forward spec can come from a config file discovered in the working
        directory, so what it opened is not necessarily what its user asked
        for.
        """
        said = []
        if self.exposed:
            said.append(
                f"{', '.join(self.exposed)} is bound to every interface, so "
                "anything that can reach this machine can reach the database "
                "through it."
            )
        if not self.resolved:
            said.append(
                "ssh did not say what it forwards, so Harlequin did not wait "
                "for a local port to answer before connecting."
            )
        return tuple(said)

    def cache_material(self) -> tuple[str, ...]:
        """What a connection reached through this tunnel is keyed by, beside itself.

        Two bastions fronting two databases both look like `localhost:15439`,
        and a catalog cache or a query history shared between them would be one
        database's answers under the other's name. The specs a caller asked for
        stand in where `ssh -G` did not say what was resolved, so two tunnels
        that forward different ports never key alike.
        """
        forwarded = (
            [f"{f.listen} {f.destination}" for f in self.forwards]
            if self.forwards
            else list(self.requested)
        )
        return (self.host, *forwarded)

    def watch(self, on_close: Callable[[str], None]) -> None:
        """Call `on_close` from a thread when the child exits on its own.

        Not called for a tunnel `stop()` took down. Set once -- a second call
        is ignored, so a caller cannot end up with two threads and two notices
        -- and every `start()` after this re-arms it.
        """
        if self._on_close is not None:
            return
        self._on_close = on_close
        self._arm_watcher()

    def start(self, *, batch_mode: bool = False, echo_stderr: bool = True) -> None:
        """Start it and block until the forwarded ports accept connections.

        `batch_mode` adds ssh's own `BatchMode=yes` for this run alone, and
        `echo_stderr` says whether ssh's output goes to ours while it opens.
        Both are a restart's, so `argv` keeps saying what the tunnel was
        configured with.

        Raises: HarlequinSshError, quoting ssh's stderr.
        """
        if self.running:
            return
        self.reused = False
        self.dropped = False
        self.echo_stderr = echo_stderr
        already_bound = frozenset(
            endpoint for endpoint in self.endpoints if _accepts(endpoint)
        )
        argv = [_client_path(self.argv[0]), *self.argv[1:]]
        if batch_mode and "BatchMode=yes" not in argv:
            # ahead of the destination, which ssh takes last
            argv = [*argv[:-1], "-o", "BatchMode=yes", argv[-1]]
        try:
            process = subprocess.Popen(
                argv,
                # ssh's prompts go to the terminal it inherits, not to this
                # pipe, so capturing stderr does not swallow them
                # both, because ssh is not the only program writing here: a
                # `ProxyCommand` helper inherits these and picks its own
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            raise HarlequinSshError(
                f"Harlequin could not run {argv[0]}: {e}", title=ERROR_TITLE
            ) from e
        self._process = process
        self._stderr = _Output(process.stderr, echo=self.echo_stderr)
        self._stdout = _Output(process.stdout, echo=self.echo_stderr)
        _LIVE.add(self)
        atexit.register(self.stop)
        try:
            self._wait_until_ready(already_bound=already_bound)
        except BaseException:
            self.stop()
            raise
        self._arm_watcher()

    def restart(self) -> None:
        """Open it again under `BatchMode=yes`, with ssh's stderr in the error.

        Something owns the terminal by now, so a passphrase prompt has nowhere
        to go and nobody is reading ssh's stderr.

        One caller at a time: a second one that was waiting finds the forward
        already open and returns, rather than losing the bind race to the first
        and concluding the tunnel is gone.

        Raises: HarlequinSshError, after marking this tunnel as not to be
        retried.
        """
        with self._restart_lock:
            if not self.needs_restart:
                return
            self.stop()
            try:
                self.start(batch_mode=True, echo_stderr=False)
            except BaseException:
                self.dropped = True
                self.restart_failed = True
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
        for stream in (self._stderr, self._stdout):
            if stream is not None:
                stream.close()

    def __enter__(self) -> SshTunnel:
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def _arm_watcher(self) -> None:
        """Start the thread that waits on the child, if anything is listening.

        On the child existing rather than on it still running: the IDE watches
        from `on_mount`, long after `start()` returned, and a child that died
        in between is exactly what there is to report. A reused listener is not
        watched -- its child exited by design, and it is not ours to call dead.
        """
        if self._on_close is None or self._process is None or self.reused:
            return
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        process, stderr, on_close = self._process, self._stderr, self._on_close
        if process is None or on_close is None:
            return
        process.wait()
        if self._process is not process:
            # `stop()` clears it, and `start()` replaces it: either way the
            # child this thread waited on is not the tunnel's any more
            return
        self.dropped = True
        last_line = ""
        if stderr is not None:
            stderr.settle()
            last_line = stderr.last_line()
        on_close(f"tunnel closed: {last_line}" if last_line else "tunnel closed")

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
        for stream in (self._stderr, self._stdout):
            if stream is not None:
                stream.settle()
        return HarlequinSshError(
            f"ssh exited with code {self._process.returncode} without opening "
            f"the forward.\n\n{self._unseen()}".rstrip(),
            title=ERROR_TITLE,
        )

    def _timed_out(
        self, *, already_bound: Collection[tuple[str, int]] = ()
    ) -> HarlequinSshError:
        for stream in (self._stderr, self._stdout):
            if stream is not None:
                # a helper's last line may have no newline of its own
                stream.flush()
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
        if self.echo_stderr:
            return ""
        said = [
            stream.text()
            for stream in (self._stderr, self._stdout)
            if stream is not None and stream.text()
        ]
        return "\n".join(said)


class _Output:
    """One of ssh's streams: passed through to *our stderr* as it arrives, and kept.

    Passed through because the thing a user most needs to see arrives while the
    tunnel is still opening and nothing has failed yet -- Tailscale's "to
    authenticate, visit ..." among them -- and a helper's instructions are
    worthless after the timeout they caused. `echo=False` is for a caller that
    no longer owns the screen; it keeps the text for an error to quote instead.

    Always onto stderr, whichever of ssh's streams this is: `hsql`'s stdout
    carries result sets and nothing else, and a helper that writes to ssh's
    stdout must not be able to corrupt a caller's csv.

    A line at a time, so `redact_text()` sees whole lines: ssh is third-party
    output, and a driver's DSN can reach it. `_readable()` takes the control
    characters out for the same reason.
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
        """What ssh said, as an error may quote it: redacted, and printable."""
        raw = b"".join(self._chunks).decode("utf-8", errors="replace")
        return redact_text(_readable(raw)).strip()

    def last_line(self) -> str:
        """The last line ssh wrote. `text()` is stripped, so none of them is blank."""
        lines = self.text().splitlines()
        return lines[-1] if lines else ""

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
        text = redact_text(_readable(raw.decode("utf-8", errors="replace")))
        with contextlib.suppress(OSError, ValueError):
            sys.stderr.write(text)
            sys.stderr.flush()


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


def _forwards(value: Any) -> tuple[str, ...]:
    """One config value as the forward specs `ssh -L` takes.

    A profile can put anything under the key: one spec, a list of them, or
    something that is neither. An entry with nothing in it is refused rather
    than passed on, since `ssh` answers `-L ""` by refusing the whole run.

    Raises: HarlequinConfigError for a value that is not a spec or a list of them.
    """
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        raise HarlequinConfigError(
            f"ssh_forward={value!r} is not a forward spec, or a list of them.",
            title=CONFIG_ERROR_TITLE,
        )
    forwards = []
    for forward in value:
        spec = _text(forward, key="ssh_forward")
        if spec is None:
            raise HarlequinConfigError(
                "ssh_forward has an entry with nothing in it. Spell each one "
                "LOCAL:HOST:REMOTE, or leave the key out.",
                title=CONFIG_ERROR_TITLE,
            )
        forwards.append(spec)
    return tuple(forwards)


def _flag(value: Any, *, key: str) -> bool:
    """One config value as the boolean its key takes.

    Only a boolean: TOML has one, and reading `"false"` as true is the kind of
    wrong a user cannot see in their own file.

    Raises: HarlequinConfigError for anything else.
    """
    if value is None:
        return False
    if not isinstance(value, bool):
        raise HarlequinConfigError(
            f"{key}={value!r} is not true or false.", title=CONFIG_ERROR_TITLE
        )
    return value


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


def find_client(program: str = SSH) -> str | None:
    """`program` as a path, resolved against PATH and nothing else, or None.

    Not `shutil.which()`, which prepends the working directory on Windows --
    where `CreateProcess` searches it ahead of PATH anyway, so a repository
    that ships an `ssh.exe` would run in place of the user's client. A program
    that already names a path is returned as it is.
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
    return None


def _client_path(program: str) -> str:
    """`program` as something to spawn: a resolved path, or the name it was.

    A name PATH does not have is handed to `Popen` as it is, so the spawn
    raises where it always did.
    """
    return find_client(program) or program


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


def _is_exposed(field: str) -> bool:
    """Whether an `ssh -G` listen field binds somewhere other than loopback.

    A field with no host is loopback: that is what ssh binds a bare port to. A
    socket path is not an interface, so it is not one either.
    """
    split = _split(field)
    if split is None:
        return False
    host = split[0]
    return host not in ("", "localhost", "::1", "[::1]") and not host.startswith("127.")


def _readable(text: str) -> str:
    """`text` with the control characters a terminal would act on removed.

    ssh's stderr carries a server's pre-auth banner and a helper's output
    verbatim, and both are chosen by whatever the config file pointed at: an
    escape sequence in either would drive the terminal it is printed to, or
    become markup in the widget it is shown in.
    """
    return _CONTROL.sub("", text)


def _accepts(endpoint: tuple[str, int]) -> bool:
    """Whether something is listening on a local port, right now."""
    try:
        with socket.create_connection(endpoint, timeout=_CONNECT_TIMEOUT):
            return True
    except OSError:
        return False
