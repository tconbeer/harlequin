"""A tunnel that drops mid-session, and the connection that died with it.

Restarting `ssh` is not enough on its own -- the adapter's TCP connection ran
*through* the old forward -- so the recovery is both halves, and it happens
lazily, before the next thing that needs the database.
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

import pytest

from harlequin import Harlequin
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


@pytest.fixture
def dropping_tunnel(monkeypatch: pytest.MonkeyPatch) -> SshTunnel:
    """A tunnel whose child holds its forward for a moment and then dies."""
    monkeypatch.setenv("FAKE_SSH_LIFETIME", "0.4")
    port = free_port()
    tunnel = SshTunnel(
        [
            sys.executable,
            str(FAKE_SSH),
            "-N",
            "-L",
            f"{port}:db.internal:5432",
            "web-1",
        ],
        forwards=(Forward(str(port), "[db.internal]:5432"),),
        host="web-1",
    )
    tunnel.start()
    return tunnel


@pytest.mark.asyncio
async def test_a_dropped_tunnel_is_reopened_before_the_next_thing_that_needs_it(
    app: Harlequin,
    dropping_tunnel: SshTunnel,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.ssh_tunnel = dropping_tunnel
    try:
        async with app.run_test() as pilot:
            await wait_for_workers(app)
            first_connection = app.connection
            assert first_connection is not None

            assert wait_until(lambda: dropping_tunnel.needs_restart)
            # the child that comes back stays up
            monkeypatch.delenv("FAKE_SSH_LIFETIME")

            app.action_refresh_catalog()
            await wait_for_workers(app)
            await pilot.pause()

            assert dropping_tunnel.running
            assert accepts(dropping_tunnel.endpoints[0][1])
            # both halves: a new session, on a forward that is open again
            assert app.connection is not None
            assert app.connection is not first_connection
    finally:
        dropping_tunnel.stop()


@pytest.mark.asyncio
async def test_a_tunnel_that_will_not_come_back_is_not_tried_twice(
    app: Harlequin,
    dropping_tunnel: SshTunnel,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry storm against a bastion that is down is how an account gets locked."""
    app.ssh_tunnel = dropping_tunnel
    try:
        async with app.run_test() as pilot:
            await wait_for_workers(app)
            assert wait_until(lambda: dropping_tunnel.needs_restart)
            monkeypatch.setenv("FAKE_SSH_STDERR", "Permission denied (publickey).")
            monkeypatch.setenv("FAKE_SSH_EXIT", "255")

            app.action_refresh_catalog()
            await wait_for_workers(app)
            await pilot.pause()
            assert not dropping_tunnel.needs_restart
            # said once, in ssh's own words
            assert len(app.screen_stack) == 2
            await pilot.press("escape")
            await pilot.pause()

            app.action_refresh_catalog()
            await wait_for_workers(app)
            await pilot.pause()
            assert not dropping_tunnel.running
            assert len(app.screen_stack) == 1
    finally:
        dropping_tunnel.stop()
