"""`harlequin.history`: the query log, as the History screen reads it.

One read per screen, and the records it builds are what a human scrolls -- so
what this pins is the mapping: newest first, the store's UTC in the reader's
own time zone, and a run whose rows were never counted rendering as something
rather than as a crash.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest
from rich.console import Console

from harlequin.catalog_cache import migrate_pickled_history
from harlequin.history import History, QueryExecution
from harlequin.query_log import QueryLog


@pytest.fixture
def store(query_log_path: Path) -> Path:
    """The throwaway store every default path in this module already points at."""
    return query_log_path


@pytest.fixture
def log(store: Path) -> QueryLog:
    return QueryLog(program="harlequin", connection="abc123")


def programs(store: Path) -> set[str]:
    """Which command every row in the store says ran it."""
    db = sqlite3.connect(store)
    try:
        return {program for (program,) in db.execute("select program from queries")}
    finally:
        db.close()


def rendered(record: QueryExecution) -> str:
    console = Console(width=80, no_color=True)
    with console.capture() as capture:
        console.print(record)
    return capture.get()


def test_recent_returns_the_newest_queries_first(log: QueryLog) -> None:
    for sql in ("select 1", "select 2", "select 3"):
        log.write(sql, rows=1, elapsed_ms=12.5)

    assert [record.query_text for record in History.recent()] == [
        "select 3",
        "select 2",
        "select 1",
    ]


def test_recent_reads_only_the_connection_it_was_asked_for(store: Path) -> None:
    """The screen shows this database's queries, whichever command ran them."""
    for connection, sql in (("mine", "select 1"), ("theirs", "select 2")):
        other = QueryLog(program="hsql", connection=connection)
        other.write(sql)
        other.close()

    assert [record.query_text for record in History.recent(connection="mine")] == [
        "select 1"
    ]


def test_recent_takes_its_rows_from_the_newest_end(log: QueryLog) -> None:
    for i in range(10):
        log.write(f"select {i}")

    assert [record.query_text for record in History.recent(n=2)] == [
        "select 9",
        "select 8",
    ]


def test_recent_searches_the_stored_sql(log: QueryLog) -> None:
    log.write("select * from line_items")
    log.write("select * from orders")

    assert [record.query_text for record in History.recent(search="line_items")] == [
        "select * from line_items"
    ]


def test_recent_reads_a_store_that_is_not_there_as_no_history(store: Path) -> None:
    assert not store.exists()
    assert len(History.recent()) == 0


def test_a_record_keeps_what_the_run_it_describes_did(log: QueryLog) -> None:
    log.write("select 1", rows=42, elapsed_ms=1500.0)

    (record,) = History.recent()
    assert record.query_text == "select 1"
    assert record.result_row_count == 42
    # the store keeps milliseconds; a record is rendered in seconds
    assert record.elapsed == 1.5
    assert record.status == "ok"


def test_a_stored_time_is_read_in_the_readers_own_zone(log: QueryLog) -> None:
    """Both commands write UTC, and a human reads the clock on their wall."""
    ran_at = datetime(2024, 1, 26, 18, 30, tzinfo=timezone.utc)
    log.write("select 1", run_at=ran_at)

    (record,) = History.recent()
    assert record.executed_at == ran_at.astimezone()


def test_a_failed_query_renders_as_an_error(log: QueryLog) -> None:
    log.write("sel", status="error", error="Parser Error")

    (record,) = History.recent()
    assert record.status == "error"
    assert "ERROR" in rendered(record)


def test_a_canceled_query_is_not_an_error(log: QueryLog) -> None:
    """The distinction the store keeps is one the screen can show."""
    log.write("select pg_sleep(60)", status="canceled")

    (record,) = History.recent()
    assert "CANCELED" in rendered(record)


def test_a_query_whose_rows_were_never_counted_still_renders(log: QueryLog) -> None:
    """DDL, and a session that died between running a query and fetching it."""
    log.write("create table foo (a int)")

    (record,) = History.recent()
    assert record.result_row_count is None
    assert record.elapsed is None
    assert "SUCCESS" in rendered(record)


def test_a_row_with_an_unreadable_timestamp_costs_only_that_row(
    log: QueryLog, store: Path
) -> None:
    log.write("select 1")
    log.write("select 2")
    db = sqlite3.connect(store)
    with db:
        db.execute("update queries set run_at = 'yesterday' where sql = 'select 2'")
    db.close()

    assert [record.query_text for record in History.recent()] == ["select 1"]


# --- the one-time move out of the pickle -------------------------------------


@pytest.fixture
def pickled(write_legacy_history: Callable[..., None]) -> datetime:
    """Two queries and an error, in a cache of the version that pickled them."""
    ran_at = datetime.now() - timedelta(days=30)
    write_legacy_history(
        "abc123",
        ("select 1", ran_at, 1, 0.5),
        ("sel", ran_at + timedelta(minutes=1), -1, 0.0),
    )
    return ran_at


def test_a_pickled_history_moves_into_the_store(pickled: datetime, store: Path) -> None:
    assert migrate_pickled_history("abc123") == 2

    records = list(History.recent(connection="abc123"))
    assert [record.query_text for record in records] == ["sel", "select 1"]
    # its own timestamp, months old, rather than the moment it was moved
    assert records[-1].executed_at == pickled.astimezone()
    assert records[-1].result_row_count == 1
    assert records[0].status == "error"
    assert programs(store) == {"harlequin"}


def test_a_pickled_history_moves_once(pickled: datetime, store: Path) -> None:
    assert migrate_pickled_history("abc123") == 2
    assert migrate_pickled_history("abc123") == 0
    assert len(History.recent(connection="abc123")) == 2


def test_a_connection_that_has_run_something_is_left_alone(
    pickled: datetime, store: Path
) -> None:
    """A store with rows is the newer record, whichever command wrote them."""
    log = QueryLog(program="hsql", connection="abc123")
    log.write("select 2")
    log.close()

    assert migrate_pickled_history("abc123") == 0
    assert [record.query_text for record in History.recent()] == ["select 2"]


def test_a_connection_the_cache_does_not_hold_moves_nothing(
    pickled: datetime, store: Path
) -> None:
    assert migrate_pickled_history("other") == 0
    assert len(History.recent()) == 0


def test_there_is_nothing_to_move_without_a_pickle(
    catalog_cache_dir: Path, store: Path
) -> None:
    assert migrate_pickled_history("abc123") == 0
    assert not store.exists()
