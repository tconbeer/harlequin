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
from textual.pilot import Pilot
from textual.widgets._tree import TreeNode

from harlequin import Harlequin
from harlequin.app import QuerySubmitted
from harlequin.catalog import CatalogItem, InteractiveCatalogItem
from harlequin.components.data_catalog.database_tree import DatabaseTree
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


async def _first_database_node(pilot: Pilot, app: Harlequin) -> TreeNode[CatalogItem]:
    """The tree's first database node, once the catalog has been rendered.

    `wait_for_workers` returns when `get_catalog()` has answered, which is
    before the message that reloads the tree has been handled.
    """
    tree = app.data_catalog.database_tree
    for _ in range(200):
        if tree.root.children:
            return tree.root.children[0]
        await pilot.pause(0.05)
    raise AssertionError("the data catalog never rendered a database")


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
async def test_a_reconnect_leaves_a_catalog_that_still_loads(
    app_small_sqlite: Harlequin,
    dropping_tunnel: SshTunnel,
    drop_trigger: Path,
    schema_updates: list[None],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    expand_catalog_node: Callable[[Pilot, TreeNode[CatalogItem]], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query that reopens a dropped tunnel leaves a catalog that still loads.

    sqlite is the adapter here because its `close()` really closes the driver
    connection, so an item left on the old one fails deterministically rather
    than by luck.
    """
    # the node under test has to still be unloaded when the tunnel drops, and
    # the prefetch scan would have loaded it during start-up
    monkeypatch.setattr(DatabaseTree, "_schedule_prefetch_scan", lambda _self: None)
    app = app_small_sqlite
    app.ssh_tunnel = dropping_tunnel
    try:
        async with app.run_test() as pilot:
            await wait_for_workers(app)
            first_connection = app.connection
            first_catalog = app.data_catalog.database_tree.catalog
            assert first_connection is not None
            assert first_catalog is not None
            db_node = await _first_database_node(pilot, app)
            assert isinstance(db_node.data, InteractiveCatalogItem)
            assert not db_node.data.loaded

            drop_trigger.touch()
            assert wait_until(lambda: dropping_tunnel.needs_restart)
            monkeypatch.delenv("FAKE_SSH_DROP_WHEN")

            # a query is the recovery's other trigger; a refresh would rebuild
            # the tree on its own, which is the workaround this replaces
            schema_updates.clear()
            app.post_message(QuerySubmitted(queries=["select 1"], limit=None))
            await pilot.pause()
            await wait_for_workers(app)
            for _ in range(200):
                if app.data_catalog.database_tree.catalog is not first_catalog:
                    break
                await pilot.pause(0.05)

            assert len(schema_updates) == 1
            assert app.connection is not first_connection
            assert app.data_catalog.database_tree.catalog is not first_catalog
            db_node = await _first_database_node(pilot, app)
            assert isinstance(db_node.data, InteractiveCatalogItem)
            assert db_node.data.connection is app.connection

            # the symptom in #1127: expanding a node that had not loaded yet
            await expand_catalog_node(pilot, db_node)
            assert db_node.data.loaded
            assert len(app.screen_stack) == 1  # no Catalog Error modal
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
            first_catalog = app.data_catalog.database_tree.catalog
            assert first_catalog is not None

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
            assert app.data_catalog.database_tree.catalog is not first_catalog
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
            # no catalog is coming, so the refresh's spinner has to stop
            assert not app.data_catalog.database_tree.loading
    finally:
        dropping_tunnel.stop()
