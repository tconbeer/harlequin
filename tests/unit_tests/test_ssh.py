"""The `ssh` child's lifetime, and the one thing Harlequin parses: `ssh -G`.

No SSH server, and no `ssh` binary except in the one test that asks a real
client whether it accepts the argv. Everything else hands `SshTunnel` a Python
child that binds the local end of a forward and sleeps, which is every
observable thing a real forward does to the machine Harlequin runs on.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Iterator, Sequence
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner, Result

from harlequin.cli import build_cli as harlequin_cli
from harlequin.config import DEFAULT_SSH_TIMEOUT, SSH_KEYS, take_ssh_keys
from harlequin.exception import HarlequinConfigError, HarlequinSshError
from harlequin.hsql.cli import build_cli as hsql_cli
from harlequin.hsql.diagnostics import ExitCode
from harlequin.ssh import (
    Forward,
    SshOptions,
    SshTunnel,
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
        ("127.0.0.1", 15439),
        ("127.0.0.1", 15440),
        ("::1", 15441),
        None,
    ]
    assert str(config.forwards[0]) == "127.0.0.1:15439 -> data.example.com:5439"


def test_a_wildcard_bind_is_polled_on_the_loopback() -> None:
    config = parse_config("hostname web-1\nlocalforward [0.0.0.0]:15439 [db]:5439\n")
    assert config is not None
    assert config.forwards[0].endpoint == ("127.0.0.1", 15439)


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
    with pytest.raises(HarlequinConfigError, match="--ssh-forward") as excinfo:
        build_tunnel("redshift_prod")
    assert "LocalForward" in str(excinfo.value)


@posix_only
def test_a_forward_from_the_ssh_config_is_the_one_waited_on(
    fake_ssh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The motivating case: Harlequin was never told a port number."""
    monkeypatch.setenv("FAKE_SSH_FORWARD", "15439:data.example.com:5439")
    tunnel = build_tunnel("redshift_prod")
    assert tunnel.endpoints == (("127.0.0.1", 15439),)
    assert tunnel.describe() == (
        "127.0.0.1:15439 -> data.example.com:5439 via redshift_prod"
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_SSH_STDERR", "Permission denied (publickey).")
    monkeypatch.setenv("FAKE_SSH_EXIT", "255")
    with pytest.raises(HarlequinSshError, match="Permission denied"):
        child_tunnel(free_port()).start()


def test_a_port_that_is_already_bound_fails(listener: int) -> None:
    with pytest.raises(HarlequinSshError, match="[Aa]ddress already in use"):
        child_tunnel(listener).start()


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
    assert [forward.endpoint for forward in config.forwards] == [("127.0.0.1", 15439)]


def test_the_lifecycle_helpers_agree_about_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fake client binds exactly the ports `-G` said it would."""
    ports: Sequence[int] = (free_port(), free_port())
    with child_tunnel(*ports):
        assert all(accepts(port) for port in ports)


# both commands, end to end, against a fake client on PATH


@pytest.fixture
def ssh_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A fake `ssh` the real CLI path will find, and a log of how it was called."""
    monkeypatch.setenv("PATH", f"{FAKE_SSH.parent}{os.pathsep}{os.environ['PATH']}")
    record = tmp_path / "argv.jsonl"
    monkeypatch.setenv("FAKE_SSH_ARGV", str(record))
    return record


def calls(record: Path) -> list[list[str]]:
    """Every argv the fake client was run with, probe included."""
    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text().splitlines()]


def run_hsql(*args: str) -> Result:
    argv = [str(arg) for arg in args]
    return CliRunner().invoke(hsql_cli(argv), argv, catch_exceptions=False)


def run_harlequin(*args: str) -> Result:
    argv = [str(arg) for arg in args]
    return CliRunner().invoke(harlequin_cli(argv), argv, catch_exceptions=False)


TUNNELED = ["--ssh-host", "redshift_prod", "--ssh-forward"]
DUCK = ["-a", "duckdb", "--no-init", ":memory:"]


@posix_only
def test_hsql_runs_sql_through_a_tunnel(
    ssh_on_path: Path, no_discovered_config: None
) -> None:
    port = free_port()
    result = run_hsql(
        *DUCK, *TUNNELED, f"{port}:data.example.com:5439", "-c", "select 1 as one"
    )
    assert result.exit_code == 0
    assert "one" in result.output
    assert f"ssh: 127.0.0.1:{port} -> data.example.com:5439 via redshift_prod" in (
        result.stderr
    )
    # the child is gone with the run, rather than left for the user to kill
    assert not accepts(port)


@posix_only
def test_the_forward_reaches_ssh_unchanged_and_repeats_in_order(
    ssh_on_path: Path, no_discovered_config: None
) -> None:
    specs = [f"{free_port()}:one.internal:5439", f"{free_port()}:two.internal:5440"]
    result = run_hsql(
        *DUCK,
        "--ssh-host",
        "tco@web-1",
        *[arg for spec in specs for arg in ("--ssh-forward", spec)],
        "--ssh-batch-mode",
        "-c",
        "select 1",
    )
    assert result.exit_code == 0
    argv = calls(ssh_on_path)[-1]
    assert argv[-1] == "tco@web-1"
    assert [argv[i + 1] for i, arg in enumerate(argv) if arg == "-L"] == specs
    assert "BatchMode=yes" in argv


@posix_only
def test_a_destination_that_forwards_nothing_is_a_usage_error(
    ssh_on_path: Path, no_discovered_config: None
) -> None:
    result = run_hsql(*DUCK, "--ssh-host", "redshift_prod", "-c", "select 1")
    assert result.exit_code == ExitCode.USAGE
    assert "--ssh-forward" in result.stderr
    assert "LocalForward" in result.stderr


@posix_only
def test_a_tunnel_that_will_not_open_exits_3_quoting_ssh(
    ssh_on_path: Path, no_discovered_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SSH_STDERR", "Permission denied (publickey).")
    monkeypatch.setenv("FAKE_SSH_EXIT", "255")
    result = run_hsql(
        *DUCK, *TUNNELED, f"{free_port()}:db.internal:5432", "-c", "select 1"
    )
    assert result.exit_code == ExitCode.CONNECTION
    assert "Permission denied (publickey)." in result.stderr
    assert result.stdout == ""


@posix_only
def test_the_tunnel_is_not_opened_for_a_mode_that_never_connects(
    ssh_on_path: Path, no_discovered_config: None
) -> None:
    result = run_hsql("-a", "duckdb", *TUNNELED, "15439:db.internal:5432", "--info")
    assert result.exit_code == 0
    assert calls(ssh_on_path) == []


@posix_only
def test_a_profile_carries_the_tunnel_and_the_details_that_need_it(
    ssh_on_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.1: one key beside the connection details that already work."""
    port = free_port()
    config = tmp_path / ".harlequin.toml"
    config.write_text(
        "[profiles.redshift]\n"
        'adapter = "duckdb"\n'
        "no_init = true\n"
        'ssh_host = "redshift_prod"\n'
        f'ssh_forward = ["{port}:data.example.com:5439"]\n'
    )
    monkeypatch.setattr(
        "harlequin.config._search_directories",
        lambda: [(tmp_path, (".harlequin.toml",))],
    )
    result = run_hsql("-P", "redshift", ":memory:", "-c", "select 1 as one")
    assert result.exit_code == 0, result.stderr
    assert "redshift_prod" in result.stderr
    assert calls(ssh_on_path)[-1][-1] == "redshift_prod"


@posix_only
def test_the_adapter_is_never_told_a_tunnel_exists(
    ssh_on_path: Path, no_discovered_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract: the connection details reach the adapter exactly as typed."""
    from harlequin.plugins import load_adapter

    handed: dict[str, object] = {}

    def spy(name: str) -> type:
        adapter_cls = load_adapter(name)

        class Recording(adapter_cls):  # type: ignore[valid-type,misc]
            def __init__(self, **kwargs: object) -> None:
                handed.update(kwargs)
                super().__init__(**kwargs)

        return Recording

    monkeypatch.setattr("harlequin.hsql.cli.load_adapter", spy)
    result = run_hsql(
        *DUCK, *TUNNELED, f"{free_port()}:data.example.com:5439", "-c", "select 1"
    )
    assert result.exit_code == 0, result.stderr
    assert handed["conn_str"] == (":memory:",)
    assert not [key for key in handed if key.startswith("ssh")]


@posix_only
def test_the_ide_opens_the_tunnel_before_it_starts(
    ssh_on_path: Path, no_discovered_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = MagicMock()
    monkeypatch.setattr("harlequin.cli.Harlequin", app)
    port = free_port()
    result = run_harlequin(
        "-a",
        "duckdb",
        "--no-init",
        ":memory:",
        *TUNNELED,
        f"{port}:data.example.com:5439",
    )
    assert result.exit_code == 0
    tunnel = app.call_args.kwargs["ssh_tunnel"]
    assert tunnel is not None
    assert tunnel.host == "redshift_prod"
    # started before the app was built, and stopped when it returned
    assert app.return_value.run.called
    assert not accepts(port)


@posix_only
def test_two_bastions_do_not_share_a_catalog_cache(
    ssh_on_path: Path, no_discovered_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both look like `localhost:15439`, and the details cannot tell them apart."""
    app = MagicMock()
    monkeypatch.setattr("harlequin.cli.Harlequin", app)
    forward = f"{free_port()}:data.example.com:5439"
    hashes = []
    for host in ("bastion_one", "bastion_two"):
        run_harlequin(
            "-a",
            "duckdb",
            "--no-init",
            ":memory:",
            "--ssh-host",
            host,
            "--ssh-forward",
            forward,
        )
        hashes.append(app.call_args.kwargs["connection_hash"])
    assert hashes[0] != hashes[1]


def test_both_commands_take_the_same_ssh_options() -> None:
    """One profile serves both, so a spelling that drifted would only work in one."""

    def spellings(command: click.Command) -> dict[str, tuple[str, ...]]:
        return {
            param.name: tuple(param.opts)
            for param in command.params
            if param.name is not None and param.name.startswith("ssh_")
        }

    ide = spellings(harlequin_cli([]))
    headless = spellings(hsql_cli([]))
    assert set(ide) == set(SSH_KEYS)
    assert ide == headless


def test_the_ssh_timeout_is_the_one_default_both_commands_carry() -> None:
    assert SshOptions.from_config({}).timeout == DEFAULT_SSH_TIMEOUT


@pytest.mark.parametrize(
    "config",
    [
        {"ssh_forward": ["15439:db:5432"]},
        {"ssh_batch_mode": True},
        {"ssh_timeout": 30},
    ],
)
def test_a_tunnel_with_no_destination_is_refused(config: dict) -> None:
    with pytest.raises(HarlequinConfigError, match="ssh_host"):
        take_ssh_keys(dict(config))


def test_the_ssh_keys_come_off_whatever_else_a_profile_holds() -> None:
    profile = {"host": "localhost", "port": 15439, "ssh_host": "web-1"}
    taken = take_ssh_keys(profile)
    assert profile == {"host": "localhost", "port": 15439}
    assert taken == {"ssh_host": "web-1"}


@pytest.mark.parametrize("value", [42, ["a", "b"], {"x": 1}])
def test_a_destination_that_is_not_text_is_refused(value: object) -> None:
    with pytest.raises(HarlequinConfigError, match="ssh_host"):
        SshOptions.from_config({"ssh_host": value})


def test_a_profile_may_write_one_forward_as_a_string() -> None:
    options = SshOptions.from_config(
        {"ssh_host": "web-1", "ssh_forward": "15439:db:5432"}
    )
    assert options.forwards == ("15439:db:5432",)
