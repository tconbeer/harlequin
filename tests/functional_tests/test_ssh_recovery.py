"""A tunnel that drops mid-session, and the connection that died with it.

Restarting `ssh` is not enough on its own -- the adapter's TCP connection ran
*through* the old forward -- so the recovery is both halves, and it happens
lazily, before the next thing that needs the database.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Awaitable, Callable, Iterator

import pytest
from textual.pilot import Pilot

from harlequin import Harlequin
from harlequin.app import QuerySubmitted
from harlequin.catalog import InteractiveCatalogItem
from harlequin.components.data_catalog.database_tree import DatabaseTree
from harlequin.components.text_modal import ErrorModal
from harlequin.ssh import Forward, SshTunnel
from tests.functional_tests.helpers import (
    expand_catalog_node,
    first_database_node,
    rendered_catalog,
    replaced_catalog,
)
from tests.tunnels import ssh_child_exited
from tests.waiting import POLL_INTERVAL, accepts, on_a_free_port, wait_for

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake client binds on a POSIX loopback"
)


def _modal_text(app: Harlequin) -> str:
    """What the error modal on top of the stack is showing."""
    modal = app.screen_stack[-1]
    assert isinstance(modal, ErrorModal)
    return modal.text


async def _wait_for_drop(pilot: Pilot, tunnel: SshTunnel) -> None:
    """Wait for the watcher thread to notice the child let the forward go."""
    await wait_for(
        pilot,
        lambda: tunnel.needs_restart,
        description="the tunnel to be reported dropped",
        interval=POLL_INTERVAL,
    )


@pytest.fixture
def dropping_tunnel(drop_trigger: Path, fake_ssh_client: Path) -> Iterator[SshTunnel]:
    """A tunnel whose child holds its forward until `drop_trigger` is touched."""

    def start(port: int) -> SshTunnel:
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

    tunnel = on_a_free_port(start, retry_on=ssh_child_exited)
    try:
        yield tunnel
    finally:
        tunnel.stop()


@pytest.mark.asyncio
async def test_a_dropped_tunnel_is_reopened_before_the_next_thing_that_needs_it(
    app: Harlequin,
    dropping_tunnel: SshTunnel,
    drop_trigger: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.ssh_tunnel = dropping_tunnel
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        first_connection = app.connection
        assert first_connection is not None

        drop_trigger.touch()
        await _wait_for_drop(pilot, dropping_tunnel)
        # the child that comes back stays up
        monkeypatch.delenv("FAKE_SSH_DROP_WHEN")

        app.action_refresh_catalog()
        await wait_for_workers(app)
        await wait_for(
            pilot,
            lambda: (
                app.connection is not None and app.connection is not first_connection
            ),
            description="the app to adopt the connection it reconnected with",
        )

        assert dropping_tunnel.running
        # both halves: a new session, on a forward that is open again
        assert accepts(dropping_tunnel.endpoints[0][1])


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
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        first_connection = app.connection
        first_catalog = await rendered_catalog(pilot, app)
        assert first_connection is not None
        db_node = await first_database_node(pilot, app)
        assert isinstance(db_node.data, InteractiveCatalogItem)
        assert not db_node.data.loaded

        drop_trigger.touch()
        await _wait_for_drop(pilot, dropping_tunnel)
        monkeypatch.delenv("FAKE_SSH_DROP_WHEN")

        # a query is the recovery's other trigger; a refresh would rebuild
        # the tree on its own, which is the workaround this replaces
        schema_updates.clear()
        app.post_message(QuerySubmitted(queries=["select 1"], limit=None))
        await wait_for_workers(app)
        rebuilt_catalog = await replaced_catalog(pilot, app, first_catalog)

        assert len(schema_updates) == 1
        assert rebuilt_catalog is not first_catalog
        assert app.connection is not first_connection
        db_node = await first_database_node(pilot, app)
        assert isinstance(db_node.data, InteractiveCatalogItem)
        assert db_node.data.connection is app.connection

        # the symptom in #1127: expanding a node that had not loaded yet
        await expand_catalog_node(pilot, db_node)
        assert db_node.data.loaded
        assert len(app.screen_stack) == 1  # no Catalog Error modal


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
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        first_catalog = await rendered_catalog(pilot, app)

        drop_trigger.touch()
        await _wait_for_drop(pilot, dropping_tunnel)
        monkeypatch.delenv("FAKE_SSH_DROP_WHEN")

        # the refresh builds the tree on the connection it recovered, so
        # two `get_catalog()` calls would be two threads inside one adapter
        schema_updates.clear()
        app.action_refresh_catalog()
        await wait_for_workers(app)
        rebuilt_catalog = await replaced_catalog(pilot, app, first_catalog)

        assert len(schema_updates) == 1
        assert rebuilt_catalog is not first_catalog


@pytest.mark.asyncio
async def test_a_tunnel_that_will_not_come_back_is_not_tried_twice(
    app: Harlequin,
    dropping_tunnel: SshTunnel,
    drop_trigger: Path,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.ssh_tunnel = dropping_tunnel
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        drop_trigger.touch()
        await _wait_for_drop(pilot, dropping_tunnel)
        monkeypatch.setenv("FAKE_SSH_STDERR", "Permission denied (publickey).")
        monkeypatch.setenv("FAKE_SSH_EXIT", "255")

        app.action_refresh_catalog()
        await wait_for_workers(app)
        # the error modal, once, quoting ssh
        await wait_for(
            pilot,
            lambda: len(app.screen_stack) == 2,
            description="the modal that says why the tunnel stayed down",
        )
        assert not dropping_tunnel.needs_restart
        assert "Permission denied (publickey)." in _modal_text(app)
        await pilot.press("escape")
        await wait_for(
            pilot,
            lambda: len(app.screen_stack) == 1,
            description="the error modal to be dismissed",
        )

        # and the worker aborts rather than running on the connection that
        # died with the forward, which would raise a second modal
        assert app._connection_for_worker() is None

        app.action_refresh_catalog()
        await wait_for_workers(app)
        # no catalog is coming, so the refresh's spinner has to stop
        await wait_for(
            pilot,
            lambda: not app.data_catalog.database_tree.loading,
            description="the Data Catalog's spinner to stop",
        )
        assert not dropping_tunnel.running
        assert len(app.screen_stack) == 1
