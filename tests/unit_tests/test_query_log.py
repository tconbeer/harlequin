"""`harlequin.query_log`: the store both commands write, and what it promises.

Three promises, and the tests are grouped by them: a row survives a round trip
with its types intact, a schema change is a migration rather than a new file,
and nothing here ever fails the query it was recording.
"""

from __future__ import annotations

import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from harlequin.query_log import (
    MIGRATIONS,
    RETENTION_ROWS,
    SCHEMA_VERSION,
    QueryLog,
    connection_id,
    default_path,
    get_connection_hash,
)
from harlequin.redact import REDACTED, hide_secrets_in

SECRET = "hunter2-and-then-some"


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "log" / "history.db"


@pytest.fixture
def log(store: Path) -> QueryLog:
    return QueryLog(program="hsql", connection="abc123", path=store)


def rows(store: Path, columns: str = "*") -> list[dict[str, Any]]:
    db = sqlite3.connect(store)
    db.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in db.execute(f"select {columns} from queries order by id")
        ]
    finally:
        db.close()


# --- a record ----------------------------------------------------------------


def test_a_row_survives_the_round_trip(log: QueryLog, store: Path) -> None:
    log.write(
        "select 1",
        rows=42,
        truncated=True,
        elapsed_ms=12.5,
    )
    (record,) = rows(store)
    assert record["program"] == "hsql"
    assert record["connection"] == "abc123"
    assert record["sql"] == "select 1"
    assert record["status"] == "ok"
    assert record["rows"] == 42
    assert record["truncated"] == 1
    assert record["elapsed_ms"] == 12.5
    assert record["error"] is None
    # UTC, ISO-8601, and a value `datetime` reads back
    assert datetime.fromisoformat(record["run_at"]).tzinfo is not None


def test_a_statement_is_recorded_before_its_rows_are_known(
    log: QueryLog, store: Path
) -> None:
    """Which is what a session killed mid-fetch keeps."""
    row = log.write("select 1")
    (written,) = rows(store)
    assert (written["sql"], written["status"], written["rows"]) == (
        "select 1",
        "ok",
        None,
    )

    log.update(row, rows=7, truncated=False, elapsed_ms=3.0)
    (record,) = rows(store)
    assert (record["rows"], record["truncated"], record["elapsed_ms"]) == (7, 0, 3.0)


def test_updating_a_row_that_was_never_written_does_nothing(
    log: QueryLog, store: Path
) -> None:
    log.update(None, rows=7)
    assert rows(store) == []


def test_an_error_keeps_its_message(log: QueryLog, store: Path) -> None:
    log.write("select nope", status="error", error="no such column: nope")
    (record,) = rows(store)
    assert record["status"] == "error"
    assert record["error"] == "no such column: nope"
    assert record["rows"] is None


def test_a_canceled_statement_says_so(log: QueryLog, store: Path) -> None:
    """A cancelled cursor comes back empty and error-free, so only the caller
    that cancelled it can tell it from a query that matched nothing."""
    row = log.write("select * from big")
    log.update(row, status="canceled")
    assert rows(store)[0]["status"] == "canceled"


def test_a_record_can_predate_the_store(log: QueryLog, store: Path) -> None:
    """What a one-time migration out of an older history needs."""
    then = datetime.now(timezone.utc) - timedelta(days=30)
    log.write("select 1", run_at=then)
    assert datetime.fromisoformat(rows(store)[0]["run_at"]) == then


def test_a_secret_never_reaches_the_store(store: Path) -> None:
    """The store outlives the process, and a query can carry a credential."""
    hide_secrets_in({"password": SECRET})
    log = QueryLog(program="hsql", path=store)
    log.write(
        f"attach 'postgres://u:{SECRET}@host/db'",
        status="error",
        error=f"could not connect with password {SECRET}",
    )
    (record,) = rows(store)
    assert SECRET not in record["sql"]
    assert SECRET not in record["error"]
    assert REDACTED in record["sql"]


# --- the schema --------------------------------------------------------------


def test_a_new_store_arrives_at_the_current_version(log: QueryLog, store: Path) -> None:
    log.write("select 1")
    db = sqlite3.connect(store)
    try:
        assert db.execute("pragma user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        db.close()


def test_migrating_from_zero_runs_every_step(store: Path) -> None:
    """An empty file at `user_version = 0` is what a first run finds."""
    store.parent.mkdir(parents=True)
    sqlite3.connect(store).close()
    log = QueryLog(program="hsql", path=store)
    assert log.enabled
    log.write("select 1")
    assert len(rows(store)) == 1


def test_a_store_already_at_the_current_version_is_left_alone(store: Path) -> None:
    first = QueryLog(program="hsql", path=store)
    first.write("select 1")
    first.close()

    second = QueryLog(program="harlequin", path=store)
    second.write("select 2")
    assert [record["sql"] for record in rows(store)] == ["select 1", "select 2"]


def test_a_store_written_by_a_newer_harlequin_is_still_written(store: Path) -> None:
    """Its columns are a superset of these, so the inserts still name what they
    mean -- and discarding a history nothing can re-fetch is the wrong answer."""
    store.parent.mkdir(parents=True)
    db = sqlite3.connect(store)
    for statement in MIGRATIONS[0]:
        db.execute(statement)
    db.execute("alter table queries add column something_new text")
    db.execute(f"pragma user_version = {SCHEMA_VERSION + 1}")
    db.commit()
    db.close()

    log = QueryLog(program="hsql", path=store)
    log.write("select 1")
    assert log.failure is None
    assert len(rows(store)) == 1


def test_retention_trims_from_the_oldest_end(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("harlequin.query_log.RETENTION_ROWS", 5)
    first = QueryLog(program="hsql", path=store)
    for n in range(8):
        first.write(f"select {n}")
    first.close()

    # trimming is once per process, so it is the next run that finds them
    assert len(rows(store)) == 8
    QueryLog(program="hsql", path=store)
    assert [record["sql"] for record in rows(store)] == [
        f"select {n}" for n in range(3, 8)
    ]


def test_retention_keeps_a_store_smaller_than_the_cap_whole(store: Path) -> None:
    first = QueryLog(program="hsql", path=store)
    first.write("select 1")
    first.close()
    QueryLog(program="hsql", path=store)
    assert len(rows(store)) == 1


def test_the_default_store_is_one_file_under_the_state_dir() -> None:
    """One store rather than one per connection: an agent asking what has been
    run against a warehouse should not have to know a hash to find a file."""
    assert default_path().name == "history.db"
    assert default_path().parent.name == "harlequin"


# --- failure never fails a query ---------------------------------------------


def test_a_store_that_cannot_be_opened_disables_logging(tmp_path: Path) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")
    log = QueryLog(program="hsql", path=blocked / "history.db")
    assert not log.enabled
    assert log.failure is not None
    # and writing to it is a no-op rather than a raise
    assert log.write("select 1") is None
    log.update(None)
    log.close()


def test_a_store_that_is_not_a_database_disables_logging(store: Path) -> None:
    store.parent.mkdir(parents=True)
    store.write_bytes(b"this is not a SQLite file, it is a note")
    log = QueryLog(program="hsql", path=store)
    assert not log.enabled
    assert log.failure is not None


def test_a_write_that_fails_disables_logging_and_does_not_raise(
    log: QueryLog, store: Path
) -> None:
    log.write("select 1")
    db = sqlite3.connect(store)
    db.execute("drop table queries")
    db.commit()
    db.close()

    assert log.write("select 2") is None
    assert not log.enabled
    assert log.failure is not None


def test_a_disabled_log_writes_nothing(store: Path) -> None:
    """`history = false`, which is the same object to everything upstream."""
    log = QueryLog(program="hsql", path=store, enabled=False)
    assert not log.enabled
    assert log.failure is None
    assert log.write("select 1") is None
    assert not store.exists()


class _RefusesWal:
    """A connection that cannot enter WAL, which is a network home directory."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def execute(self, sql: str, *args: Any) -> Any:
        if "journal_mode" in sql:
            raise sqlite3.OperationalError("unable to open database file")
        return self._db.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    def __enter__(self) -> Any:
        return self._db.__enter__()

    def __exit__(self, *exc: Any) -> Any:
        return self._db.__exit__(*exc)


def test_the_default_journal_still_works(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store that cannot enter WAL is 34x slower per write, and just as correct."""
    connect = sqlite3.connect
    monkeypatch.setattr(
        "harlequin.query_log.sqlite3.connect",
        lambda *args, **kwargs: _RefusesWal(connect(*args, **kwargs)),
    )
    log = QueryLog(program="hsql", path=store)
    assert log.enabled, log.failure
    log.write("select 1")
    log.close()
    monkeypatch.undo()
    assert len(rows(store)) == 1


def _insert_many(store: str) -> None:
    log = QueryLog(program="hsql", path=Path(store))
    assert log.enabled, log.failure
    for n in range(100):
        log.write(f"select {n}")
    assert log.failure is None, log.failure
    log.close()


@pytest.mark.skipif(
    sys.platform == "win32", reason="a process pool per test is slow on Windows"
)
def test_several_processes_can_write_at_once(store: Path) -> None:
    """What `busy_timeout` buys, and the reason a second writer is a non-event."""
    QueryLog(program="hsql", path=store).close()
    with ProcessPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(_insert_many, str(store)) for _ in range(4)]:
            future.result()
    assert len(rows(store)) == 400


# --- what a connection is keyed by -------------------------------------------


def test_a_declared_connection_id_is_used_as_it_stands() -> None:
    assert connection_id("postgres://warehouse", ("ignored",), {}) == (
        "postgres://warehouse"
    )


def test_an_adapter_that_declares_nothing_keys_on_its_details() -> None:
    keyed = connection_id(None, ("my.db",), {"read_only": True})
    assert keyed == get_connection_hash(("my.db",), {"read_only": True})


def test_two_tunnels_to_the_same_looking_database_key_differently() -> None:
    """Both look like `localhost:15439`, and the details cannot tell them apart."""
    one = connection_id(None, ("localhost:15439",), {}, through=("bastion_one",))
    two = connection_id(None, ("localhost:15439",), {}, through=("bastion_two",))
    assert one != two


def test_an_untunneled_connection_keys_on_its_details_alone() -> None:
    assert connection_id(None, ("my.db",), {}, through=()) == get_connection_hash(
        ("my.db",), {}
    )


def test_the_retention_cap_is_the_size_the_history_screen_has_always_shown() -> None:
    """A hundred thousand rows is 45MB, and 35ms to search."""
    assert RETENTION_ROWS == 100_000
