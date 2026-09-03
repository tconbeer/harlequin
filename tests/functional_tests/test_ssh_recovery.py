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
from harlequin.app import QuerySubmitted
from harlequin.components.text_modal import ErrorModal
from harlequin.ssh import Forward, SshTunnel

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


def _modal_text(app: Harlequin) -> str:
    """What the error modal on top of the stack is showing."""
    modal = app.screen_stack[-1]
    assert isinstance(modal, ErrorModal)
    return modal.text


@pytest.fixture
def dropping_tunnel(drop_trigger: Path, fake_ssh_client: Path) -> SshTunnel:
    """A tunnel whose child holds its forward until `drop_trigger` is touched."""
    port = free_port()
    tunnel = SshTunnel(
        [
            sys.executable,
            str(fake_ssh_client),
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
    drop_trigger: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.ssh_tunnel = dropping_tunnel
    try:
        async with app.run_test() as pilot:
            await wait_for_workers(app)
            first_connection = app.connection
            assert first_connection is not None

            drop_trigger.touch()
            assert wait_until(lambda: dropping_tunnel.needs_restart)
            # the child that comes back stays up
            monkeypatch.delenv("FAKE_SSH_DROP_WHEN")

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


@pytest.fixture
def schema_updates(monkeypatch: pytest.MonkeyPatch) -> list[None]:
    """Every call to `update_schema_data`, so a double refresh is visible."""
    calls: list[None] = []
    start_worker = Harlequin.update_schema_data

    def counted(app: Harlequin) -> None:
        calls.append(None)
        start_worker(app)

    monkeypatch.setattr(Harlequin, "update_schema_data", counted)
    return calls


@pytest.mark.asyncio
async def test_a_reconnect_rebuilds_the_catalog_on_the_new_connection(
    app: Harlequin,
    dropping_tunnel: SshTunnel,
    drop_trigger: Path,
    schema_updates: list[None],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.ssh_tunnel = dropping_tunnel
    try:
        async with app.run_test() as pilot:
            await wait_for_workers(app)
            first_catalog = app.data_catalog.database_tree.catalog
            assert first_catalog is not None

            drop_trigger.touch()
            assert wait_until(lambda: dropping_tunnel.needs_restart)
            monkeypatch.delenv("FAKE_SSH_DROP_WHEN")

            # a query is the recovery's other trigger, and it leaves a tree
            # whose items load their children on the connection that died
            schema_updates.clear()
            app.post_message(QuerySubmitted(queries=["select 1"], limit=None))
            await pilot.pause()
            await wait_for_workers(app)
            await pilot.pause()

            assert len(schema_updates) == 1
            assert app.data_catalog.database_tree.catalog is not first_catalog
    finally:
        dropping_tunnel.stop()


@pytest.mark.asyncio
async def test_a_refresh_that_reconnects_does_not_ask_for_a_second_one(
    app: Harlequin,
    dropping_tunnel: SshTunnel,
    drop_trigger: Path,
    schema_updates: list[None],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.ssh_tunnel = dropping_tunnel
    try:
        async with app.run_test() as pilot:
            await wait_for_workers(app)
            drop_trigger.touch()
            assert wait_until(lambda: dropping_tunnel.needs_restart)
            monkeypatch.delenv("FAKE_SSH_DROP_WHEN")

            # the refresh builds the tree on the connection it recovered, so
            # two `get_catalog()` calls would be two threads inside one adapter
            schema_updates.clear()
            app.action_refresh_catalog()
            await wait_for_workers(app)
            await pilot.pause()

            assert len(schema_updates) == 1
            assert app.data_catalog.database_tree.catalog is not None
    finally:
        dropping_tunnel.stop()


@pytest.mark.asyncio
async def test_a_tunnel_that_will_not_come_back_is_not_tried_twice(
    app: Harlequin,
    dropping_tunnel: SshTunnel,
    drop_trigger: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.ssh_tunnel = dropping_tunnel
    try:
        async with app.run_test() as pilot:
            await wait_for_workers(app)
            drop_trigger.touch()
            assert wait_until(lambda: dropping_tunnel.needs_restart)
            monkeypatch.setenv("FAKE_SSH_STDERR", "Permission denied (publickey).")
            monkeypatch.setenv("FAKE_SSH_EXIT", "255")

            app.action_refresh_catalog()
            await wait_for_workers(app)
            await pilot.pause()
            assert not dropping_tunnel.needs_restart
            # the error modal, once, quoting ssh
            assert len(app.screen_stack) == 2
            assert "Permission denied (publickey)." in _modal_text(app)
            await pilot.press("escape")
            await pilot.pause()

            # and the worker aborts rather than running on the connection that
            # died with the forward, which would raise a second modal
            assert app._connection_for_worker() is None

            app.action_refresh_catalog()
            await wait_for_workers(app)
            await pilot.pause()
            assert not dropping_tunnel.running
            assert len(app.screen_stack) == 1
    finally:
        dropping_tunnel.stop()
