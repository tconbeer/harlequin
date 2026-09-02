"""Two threads reopening one dropped tunnel.

Recovery runs on `@work` threads in different groups, so `exclusive=True` does
not serialize it, and `restart()` takes no lock.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from harlequin.ssh import Forward, SshTunnel

FAKE_SSH = Path(__file__).parent.parent / "data" / "unit_tests" / "ssh" / "ssh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake client binds on a POSIX loopback"
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def accepts(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def wait_until(predicate: Callable[[], bool], *, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.mark.xfail(
    reason="restart() takes no lock, so a losing thread cannot tell a lost race "
    "from a tunnel that will not come back",
    strict=False,
)
def test_two_threads_reopening_one_tunnel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tunnel that came back must not be marked never-to-be-retried.

    The loser's child cannot bind a port the winner's child already took, so
    `ExitOnForwardFailure=yes` exits it 255 and `restart()` reads that as ssh
    refusing to come back.
    """
    drop_trigger = tmp_path / "drop"
    monkeypatch.setenv("FAKE_SSH_DROP_WHEN", str(drop_trigger))
    port = free_port()
    tunnel = SshTunnel(
        [sys.executable, str(FAKE_SSH), "-N", "-L", f"{port}:remote:5439", "web-1"],
        forwards=(Forward(str(port), "[remote]:5439"),),
        host="web-1",
    )
    tunnel.watch(lambda notice: None)
    tunnel.start()
    try:
        drop_trigger.touch()
        assert wait_until(lambda: tunnel.needs_restart)
        # the children that come back stay up
        monkeypatch.delenv("FAKE_SSH_DROP_WHEN")

        errors: list[BaseException] = []
        both_ready = threading.Barrier(2)

        def reopen() -> None:
            both_ready.wait()
            try:
                tunnel.restart()
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=reopen) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        assert accepts(port), "the forward is not open after two restarts"
        assert not errors, f"a restart of a tunnel that came back failed: {errors}"
        # the forward is open, so the next drop must still be recoverable
        assert not tunnel.restart_failed
    finally:
        tunnel.stop()
