"""The `ssh` child's lifetime, and the one thing Harlequin parses: `ssh -G`.

No SSH server, and no `ssh` binary except in the one test that asks a real
client whether it accepts the argv. Everything else hands `SshTunnel` the fake
client in `tests/data/unit_tests/ssh/ssh`.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Iterator, Sequence

import pytest

from harlequin.exception import HarlequinSshError
from harlequin.ssh import (
    DEFAULT_TIMEOUT,
    Forward,
    SshTunnel,
    _client_path,
    build_argv,
    build_tunnel,
    parse_config,
    resolve_config,
)

FAKE_SSH = Path(__file__).parent.parent / "data" / "unit_tests" / "ssh" / "ssh"

posix_only = pytest.mark.skipif(
    os.name == "nt", reason="a Python script is not an executable on Windows"
)


def free_port() -> int:
    """A port nothing is listening on, most likely still true a moment from now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def accepts(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def listener() -> Iterator[int]:
    """A port already bound by something that is not Harlequin's tunnel."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        yield int(sock.getsockname()[1])


def child_tunnel(*ports: int, **kwargs: object) -> SshTunnel:
    """A tunnel whose `ssh` is the fake one, run through this interpreter."""
    specs = [f"{port}:remote:5439" for port in ports]
    argv: list[str] = [sys.executable, str(FAKE_SSH), "-N"]
    for spec in specs:
        argv += ["-L", spec]
    argv.append("redshift_prod")
    forwards = tuple(Forward(str(port), "[remote]:5439") for port in ports)
    return SshTunnel(argv, forwards=forwards, host="redshift_prod", **kwargs)  # type: ignore[arg-type]


# the argv, which is the whole of what Harlequin says to ssh


def test_the_argv_forwards_nothing_it_was_not_given() -> None:
    assert build_argv("redshift_prod") == [
        "ssh",
        "-N",
        "-o",
        "ExitOnForwardFailure=yes",
        "redshift_prod",
    ]


def test_forwards_reach_the_argv_unchanged_and_in_order() -> None:
    specs = ["15439:one.internal:5439", "[::1]:15440:two.internal:5440"]
    argv = build_argv("tco@web-1", specs)
    assert argv[-1] == "tco@web-1"
    assert [argv[i + 1] for i, arg in enumerate(argv) if arg == "-L"] == specs


def test_batch_mode_is_ssh_s_own_option_and_the_only_one_added() -> None:
    argv = build_argv("web-1", batch_mode=True)
    assert "BatchMode=yes" in argv
    assert [argv[i + 1] for i, arg in enumerate(argv) if arg == "-o"] == [
        "ExitOnForwardFailure=yes",
        "BatchMode=yes",
    ]


@pytest.mark.parametrize(
    "host", ["user@web-1", "ssh://tco@web-1:2222", "redshift_prod", "10.0.0.4"]
)
def test_the_destination_is_not_parsed(host: str) -> None:
    assert build_argv(host)[-1] == host


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "-oProxyCommand=touch /tmp/pwned"},
        {"host": "web-1", "forwards": ["-oProxyCommand=touch /tmp/pwned"]},
    ],
)
def test_a_value_ssh_would_read_as_an_option_is_refused(kwargs: dict) -> None:
    with pytest.raises(HarlequinSshError, match="dash"):
        build_argv(**kwargs)


@pytest.mark.parametrize(
    "host",
    [
        "web-1`touch /tmp/pwned`",
        "web-1$(touch /tmp/pwned)",
        "tco\ntouch /tmp/pwned@web-1",
        "web-1;touch /tmp/pwned",
        "web-1 --lol",
        "web\\-1",
    ],
)
def test_a_value_a_proxycommand_would_expand_is_refused(host: str) -> None:
    """A destination reaches the user's own ssh config as %h, %r and %p.

    A `ProxyCommand` there runs them through a shell, and the destination can
    come from a config file discovered in the working directory.
    """
    with pytest.raises(HarlequinSshError, match="ProxyCommand"):
        build_argv(host)
    with pytest.raises(HarlequinSshError, match="ProxyCommand"):
        build_argv("web-1", [f"15439:{host}:5432"])


@pytest.mark.parametrize(
    "value",
    ["tco@web-1", "ssh://tco@web-1:2222", "10.0.0.4", "redshift_prod", "web-1.a.b"],
)
def test_an_ordinary_destination_is_not_refused(value: str) -> None:
    assert build_argv(value)[-1] == value


@posix_only
def test_the_client_is_resolved_against_path_not_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows searches the working directory first; a repo could ship an ssh."""
    on_path = tmp_path / "bin"
    on_path.mkdir()
    real = on_path / "ssh"
    real.write_text("#!/bin/sh\nexit 0\n")
    real.chmod(0o755)
    decoy = tmp_path / "repo"
    decoy.mkdir()
    (decoy / "ssh").write_text("#!/bin/sh\ntouch pwned\n")
    (decoy / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", str(on_path))
    monkeypatch.chdir(decoy)
    assert _client_path("ssh") == str(real)


# `ssh -G`, the one thing this feature parses


def test_forwards_parse_with_a_bind_address_ipv6_and_a_socket_path() -> None:
    config = parse_config(
        "host redshift_prod\n"
        "hostname web-1\n"
        "user tco\n"
        "localforward 15439 [data.example.com]:5439\n"
        "localforward [127.0.0.1]:15440 [other.internal]:5440\n"
        "localforward [::1]:15441 [::1]:5441\n"
        "localforward /tmp/harlequin.sock [db.internal]:5432\n"
        "remoteforward 9000 [localhost]:9000\n"
    )
    assert config is not None
    assert [forward.endpoint for forward in config.forwards] == [
        # a bare port is polled by name, so both families are tried
        ("localhost", 15439),
        ("127.0.0.1", 15440),
        ("::1", 15441),
        None,
    ]
    assert str(config.forwards[0]) == "localhost:15439 -> data.example.com:5439"


def test_a_wildcard_bind_is_polled_by_name_and_reported_as_written() -> None:
    """A forward on 0.0.0.0 reaches the whole network; the notice must say so."""
    config = parse_config("hostname web-1\nlocalforward [0.0.0.0]:15439 [db]:5439\n")
    assert config is not None
    assert config.forwards[0].endpoint == ("localhost", 15439)
    assert str(config.forwards[0]) == "0.0.0.0:15439 -> db:5439"


def test_a_config_with_only_a_dynamic_forward_forwards_nothing() -> None:
    config = parse_config("hostname web-1\ndynamicforward 1080\n")
    assert config is not None
    assert config.forwards == ()


@pytest.mark.parametrize(
    "text",
    ["", "usage: ssh [-46AaCfGg...]\n", "Permission denied (publickey).\n", "{}\n"],
)
def test_output_that_is_not_a_resolved_config_degrades(text: str) -> None:
    assert parse_config(text) is None


@posix_only
def test_a_probe_that_fails_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SSH_PROBE_EXIT", "255")
    assert resolve_config([str(FAKE_SSH), "web-1"], timeout=10) is None


@posix_only
def test_a_missing_client_degrades() -> None:
    assert resolve_config(["harlequin-no-such-ssh", "web-1"], timeout=10) is None


# building a tunnel, which is the probe plus the argv


@pytest.fixture
def fake_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("harlequin.ssh.SSH", str(FAKE_SSH))


@posix_only
def test_a_destination_that_forwards_nothing_names_both_places_to_put_one(
    fake_ssh: None,
) -> None:
    with pytest.raises(HarlequinSshError, match="--ssh-forward") as excinfo:
        build_tunnel("redshift_prod")
    assert "LocalForward" in str(excinfo.value)


@posix_only
def test_a_forward_from_the_ssh_config_is_the_one_waited_on(
    fake_ssh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The motivating case: Harlequin was never told a port number."""
    monkeypatch.setenv("FAKE_SSH_FORWARD", "15439:data.example.com:5439")
    tunnel = build_tunnel("redshift_prod")
    assert tunnel.endpoints == (("localhost", 15439),)
    assert tunnel.describe() == (
        "localhost:15439 -> data.example.com:5439 via redshift_prod"
    )


@posix_only
def test_a_probe_that_says_nothing_readable_polls_nothing(
    fake_ssh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SSH_PROBE_TEXT", "who knows\n")
    assert build_tunnel("redshift_prod").endpoints == ()


# the child's lifetime


def test_a_tunnel_holds_its_port_open_and_gives_it_back() -> None:
    port = free_port()
    with child_tunnel(port) as tunnel:
        assert accepts(port)
        assert tunnel.running
        assert not tunnel.reused
    assert not accepts(port)


def test_a_forward_that_takes_a_moment_is_waited_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_SSH_DELAY", "0.4")
    port = free_port()
    with child_tunnel(port):
        assert accepts(port)


def test_a_child_that_never_opens_the_forward_names_batch_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_SSH_HANG", "1")
    tunnel = child_tunnel(free_port(), timeout=0.5)
    with pytest.raises(HarlequinSshError, match="--ssh-batch-mode"):
        tunnel.start()
    assert not tunnel.running


def test_a_child_that_exits_is_reported_in_its_own_words(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FAKE_SSH_STDERR", "Permission denied (publickey).")
    monkeypatch.setenv("FAKE_SSH_EXIT", "255")
    with pytest.raises(HarlequinSshError, match="exited with code 255"):
        child_tunnel(free_port()).start()
    # on stderr as ssh wrote it, and so not repeated by the error
    assert "Permission denied (publickey)." in capsys.readouterr().err


def test_a_slow_client_does_not_make_a_bound_port_look_like_a_forward(
    listener: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ssh binds its forwards only after authenticating.

    A listener already on the port answers for the whole handshake, so nothing
    but the child exiting or the deadline can say whether the forward is ours.
    """
    monkeypatch.setenv("FAKE_SSH_DELAY", "1.5")
    tunnel = child_tunnel(listener)
    with pytest.raises(HarlequinSshError):
        tunnel.start()
    assert not tunnel.reused


def test_a_slow_client_still_reuses_the_listener_when_asked(
    listener: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SSH_DELAY", "1.5")
    tunnel = child_tunnel(listener, allow_reuse=True)
    tunnel.start()
    try:
        assert tunnel.reused
    finally:
        tunnel.stop()


def test_a_bound_port_that_ssh_never_answers_for_names_the_flag(
    listener: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And a timeout under the old settle window no longer reads as success."""
    monkeypatch.setenv("FAKE_SSH_HANG", "1")
    tunnel = child_tunnel(listener, timeout=0.5)
    with pytest.raises(HarlequinSshError, match="--ssh-allow-reuse") as excinfo:
        tunnel.start()
    assert "already bound" in str(excinfo.value)


def test_what_ssh_says_while_it_waits_is_shown_while_it_waits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A helper's instructions are worthless after the timeout they caused.

    Tailscale writes "to authenticate, visit ..." here, and it is a URL someone
    has to open before the tunnel can open -- so it goes to stderr as it
    arrives rather than being kept for an error.
    """
    monkeypatch.setenv(
        "FAKE_SSH_STDERR",
        "# Tailscale SSH requires an additional check.\n"
        "# To authenticate, visit: https://login.tailscale.com/a/l4fc5f072",
    )
    monkeypatch.setenv("FAKE_SSH_HANG", "1")
    tunnel = child_tunnel(free_port(), timeout=0.5)
    with pytest.raises(HarlequinSshError) as excinfo:
        tunnel.start()
    printed = capsys.readouterr().err
    assert "https://login.tailscale.com/a/l4fc5f072" in printed
    # and the error does not read it back at someone who has seen it
    assert "tailscale" not in str(excinfo.value).lower()
    assert "--ssh-batch-mode" in str(excinfo.value)


def test_what_a_helper_says_on_stdout_is_shown_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `ProxyCommand` helper inherits ssh's streams and picks its own.

    Onto our stderr either way: hsql's stdout carries result sets, and a helper
    must not be able to corrupt a caller's csv with an authentication notice.
    """
    monkeypatch.setenv("FAKE_SSH_STDOUT", "To authenticate, visit: https://example/a")
    monkeypatch.setenv("FAKE_SSH_HANG", "1")
    with pytest.raises(HarlequinSshError):
        child_tunnel(free_port(), timeout=0.5).start()
    captured = capsys.readouterr()
    assert "To authenticate, visit: https://example/a" in captured.err
    assert captured.out == ""


def test_a_line_ssh_left_unfinished_is_still_shown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A prompt has no newline of its own, and is the point of the line."""
    monkeypatch.setenv("FAKE_SSH_STDERR_PARTIAL", "Enter passphrase for key '/k/id':")
    monkeypatch.setenv("FAKE_SSH_HANG", "1")
    with pytest.raises(HarlequinSshError):
        child_tunnel(free_port(), timeout=0.5).start()
    assert "Enter passphrase for key" in capsys.readouterr().err


def test_a_caller_that_lost_the_screen_gets_the_words_in_the_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """After a restart there is no stderr a user reads, so the error carries it."""
    monkeypatch.setenv("FAKE_SSH_STDERR", "Permission denied (publickey).")
    monkeypatch.setenv("FAKE_SSH_EXIT", "255")
    tunnel = child_tunnel(free_port())
    tunnel.echo_stderr = False
    with pytest.raises(HarlequinSshError, match="Permission denied"):
        tunnel.start()
    assert "Permission denied" not in capsys.readouterr().err


def test_the_default_wait_is_long_enough_for_a_person() -> None:
    """An ssh that opens a browser for an identity provider is a slow one."""
    assert DEFAULT_TIMEOUT >= 30


def test_a_port_that_is_already_bound_fails(
    listener: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """Passing the client's own words through, whatever they are.

    Windows says "an attempt was made to access a socket in a way forbidden by
    its access permissions" where Unix says "address already in use", so what
    is asserted is that the client's own line reached the user.
    """
    with pytest.raises(HarlequinSshError):
        child_tunnel(listener).start()
    assert "bind" in capsys.readouterr().err


def test_allow_reuse_connects_through_the_existing_listener(listener: int) -> None:
    tunnel = child_tunnel(listener, allow_reuse=True)
    tunnel.start()
    try:
        assert tunnel.reused
        assert not tunnel.running
    finally:
        tunnel.stop()
    # the listener was never ours to close
    assert accepts(listener)


def test_a_half_open_reuse_fails_like_any_other(listener: int) -> None:
    """Some ports answering and some not is a state nobody meant."""
    tunnel = child_tunnel(listener, free_port(), allow_reuse=True)
    with pytest.raises(HarlequinSshError):
        tunnel.start()
    assert not tunnel.reused


def test_a_client_that_is_not_installed_says_so() -> None:
    tunnel = SshTunnel(["harlequin-no-such-ssh", "web-1"])
    with pytest.raises(HarlequinSshError, match="could not run"):
        tunnel.start()


def test_a_tunnel_with_no_port_to_poll_trusts_a_child_that_lives() -> None:
    """The degraded case: `-G` said nothing, so the adapter is the test."""
    tunnel = child_tunnel(free_port())
    tunnel.forwards = ()
    with tunnel:
        assert tunnel.running


def test_a_tunnel_with_no_port_to_poll_still_notices_a_child_that_dies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_SSH_EXIT", "255")
    tunnel = child_tunnel(free_port())
    tunnel.forwards = ()
    with pytest.raises(HarlequinSshError, match="exited"):
        tunnel.start()


def test_stopping_twice_is_not_an_error() -> None:
    tunnel = child_tunnel(free_port())
    tunnel.start()
    tunnel.stop()
    tunnel.stop()
    assert not tunnel.running


def test_a_stopped_tunnel_starts_again() -> None:
    port = free_port()
    tunnel = child_tunnel(port)
    tunnel.start()
    tunnel.stop()
    assert not accepts(port)
    tunnel.start()
    try:
        assert accepts(port)
    finally:
        tunnel.stop()


# the one test that asks a real client


def real_ssh() -> str | None:
    path = shutil.which("ssh")
    if path is None:
        return None
    try:
        subprocess.run([path, "-V"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return path


@pytest.mark.skipif(real_ssh() is None, reason="no ssh client installed")
def test_a_real_client_accepts_the_argv_and_says_what_it_would_forward() -> None:
    argv = build_argv(
        "harlequin-test-destination", ["15439:data.example.com:5439"], batch_mode=True
    )
    config = resolve_config(argv, timeout=30)
    assert config is not None
    assert [forward.endpoint for forward in config.forwards] == [("localhost", 15439)]


def test_the_lifecycle_helpers_agree_about_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fake client binds exactly the ports `-G` said it would."""
    ports: Sequence[int] = (free_port(), free_port())
    with child_tunnel(*ports):
        assert all(accepts(port) for port in ports)
