"""One SQLite store of every query both commands run, written as it runs.

Logging never fails a query: a store that cannot be opened, migrated or written
disables itself and records why in `failure`, for the caller to report once.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from platformdirs import user_state_path

from harlequin.redact import redact_conn_str, redact_sql

Status = Literal["ok", "error", "canceled"]
"""What became of one statement. Only the caller that cancelled a statement can
tell it from one that matched nothing, so `canceled` is its own status."""

MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        """
        create table queries (
          id          integer primary key,
          run_at      text    not null,
          program     text    not null,
          connection  text,
          profile     text,
          adapter     text,
          sql         text    not null,
          status      text    not null,
          rows        integer,
          truncated   integer,
          elapsed_ms  real,
          error       text
        )
        """,
        "create index queries_connection_at on queries (connection, id desc)",
    ),
)
"""Each entry takes `pragma user_version` from its index to the next, and is a
list of statements so that a migration runs inside one transaction."""

SCHEMA_VERSION = len(MIGRATIONS)

RETENTION_ROWS = 100_000
"""How many of the newest rows survive. Trimmed once per process."""

COLUMNS = (
    "run_at",
    "program",
    "connection",
    "profile",
    "adapter",
    "sql",
    "status",
    "rows",
    "truncated",
    "elapsed_ms",
    "error",
)
"""Every column a writer supplies, which is all of them but `id`."""

_INSERT = "insert into queries ({}) values ({})".format(
    ", ".join(f'"{column}"' for column in COLUMNS),
    ", ".join("?" * len(COLUMNS)),
)

BUSY_TIMEOUT_MS = 5000
"""How long a writer waits for a lock another process holds."""

UI_BUSY_TIMEOUT_MS = 250
"""What a front end that would otherwise stop redrawing waits instead.

A missed row beats a frozen window, and the row is missed only while something
else holds the store for longer than a person would sit through.
"""


def default_path() -> Path:
    """Where the store lives, for a caller that was given no path."""
    return user_state_path(appname="harlequin") / "history.db"


class QueryLog:
    """The store, open for one command's run.

    What every row of a run shares -- which program, which connection, which
    profile -- is held here rather than passed to each write, because none of
    it can change while a process is running.

    A disabled log is a real object whose writes do nothing, so that a caller
    never branches on None. Constructing one touches no disk: the first write
    opens, migrates and trims the store.
    """

    def __init__(
        self,
        *,
        program: str,
        connection: str | None = None,
        profile: str | None = None,
        adapter: str | None = None,
        enabled: bool = True,
        path: Path | None = None,
        busy_timeout_ms: int = BUSY_TIMEOUT_MS,
    ) -> None:
        self.program = program
        self.connection = connection
        self.profile = profile
        self.adapter = adapter
        self.failure: str | None = None
        """Why nothing is being logged, for a caller to report once."""
        self._path = path
        self._enabled = enabled
        self._busy_timeout_ms = busy_timeout_ms
        self._db: sqlite3.Connection | None = None
        # one connection, written from more than one thread: the IDE executes
        # and fetches on different workers, and hsql moves a run to one under
        # `--timeout`. sqlite3 does not serialize this for us.
        self._writing = threading.Lock()

    @property
    def enabled(self) -> bool:
        """Whether a write would still reach the store.

        True before the first one, which is what has not been attempted yet:
        a store that cannot be opened says so when something is written to it.
        """
        return self._enabled

    def write(
        self,
        sql: str,
        *,
        status: Status = "ok",
        rows: int | None = None,
        truncated: bool | None = None,
        elapsed_ms: float | None = None,
        error: str | None = None,
        run_at: datetime | None = None,
    ) -> int | None:
        """Record one statement, and return the row's id for `update()`.

        `run_at` is for a caller inserting a record it kept elsewhere; a live
        one leaves it out and gets now, in UTC.
        """
        moment = run_at if run_at is not None else datetime.now(timezone.utc)
        values = (
            moment.isoformat(),
            self.program,
            self.connection,
            self.profile,
            self.adapter,
            redact_sql(sql),
            status,
            rows,
            None if truncated is None else int(truncated),
            elapsed_ms,
            None if error is None else redact_sql(error),
        )
        cursor = self._run(_INSERT, values)
        return None if cursor is None else cursor.lastrowid

    def update(
        self,
        row: int | None,
        *,
        status: Status = "ok",
        rows: int | None = None,
        truncated: bool | None = None,
        elapsed_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        """Complete the row for a statement whose result is now known."""
        if row is None:
            return
        self._run(
            "update queries set status = ?, rows = ?, truncated = ?, "
            "elapsed_ms = ?, error = ? where id = ?",
            (
                status,
                rows,
                None if truncated is None else int(truncated),
                elapsed_ms,
                None if error is None else redact_sql(error),
                row,
            ),
        )

    def close(self) -> None:
        """Let go of the store.

        Writes after this are the no-ops a disabled log's are.
        """
        with self._writing:
            self._close()

    def _close(self) -> None:
        """Let go of the store, with the write lock already held."""
        if self._db is not None:
            with contextlib.suppress(sqlite3.Error):
                self._db.close()
            self._db = None
        self._enabled = False

    def _open(self) -> sqlite3.Connection | None:
        """The store, configured and migrated, or None having recorded why.

        Called by the first write, so that a front end starting up pays nothing
        for holding one.
        """
        store = self._path if self._path is not None else default_path()
        db: sqlite3.Connection | None = None
        try:
            store.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # every statement this machine has run, so not world-readable.
            # SQLite gives -wal and -shm the main file's mode. No-op on Windows.
            os.close(os.open(store, os.O_CREAT | os.O_RDWR, 0o600))
            # not thread-bound: hsql writes from the worker thread `--timeout`
            # moves a run to, and one connection is only ever written by one
            # thread at a time in either command.
            db = sqlite3.connect(store, check_same_thread=False)
            _configure(db, busy_timeout_ms=self._busy_timeout_ms)
            _migrate(db)
            _trim(db)
        except (sqlite3.Error, OSError) as e:
            if db is not None:
                with contextlib.suppress(sqlite3.Error):
                    db.close()
            self.failure = f"Harlequin could not open its query log at {store}: {e}"
            self._enabled = False
            return None
        return db

    def _run(self, sql: str, values: Sequence[Any]) -> sqlite3.Cursor | None:
        """One statement against the store, or None having disabled logging.

        The single place a write can fail, opening it included.
        """
        with self._writing:
            if not self._enabled:
                return None
            if self._db is None:
                self._db = self._open()
                if self._db is None:
                    return None
            try:
                with self._db:
                    return self._db.execute(sql, values)
            except sqlite3.Error as e:
                self.failure = f"Harlequin could not write to its query log: {e}"
                self._close()
                return None


def _configure(db: sqlite3.Connection, *, busy_timeout_ms: int) -> None:
    """The pragmas a write needs to be cheap and to survive a second writer.

    WAL wants shared memory, which a network home directory does not have; the
    default journal is slower and just as correct, so a refusal is not an error.
    """
    db.execute(f"pragma busy_timeout = {int(busy_timeout_ms)}")
    with contextlib.suppress(sqlite3.Error):
        db.execute("pragma journal_mode = wal")
    db.execute("pragma synchronous = normal")


def _migrate(db: sqlite3.Connection) -> None:
    """Bring the store up to `SCHEMA_VERSION`, one migration at a time.

    Under `begin immediate`, so that two processes opening a new store at once
    do not both create it. A newer store is left alone; its columns are a
    superset of these, so the inserts still name what they mean.
    """
    db.execute("begin immediate")
    try:
        (version,) = db.execute("pragma user_version").fetchone()
        # a negative version would slice from the end and re-run a migration
        # against a store that already has it, disabling logging for good
        version = max(version, 0)
        for index, migration in enumerate(MIGRATIONS[version:], start=version):
            for statement in migration:
                db.execute(statement)
            # a pragma takes no parameter, and `index` is this module's own
            db.execute(f"pragma user_version = {index + 1}")
    except BaseException:
        db.rollback()
        raise
    db.commit()


def _trim(db: sqlite3.Connection) -> None:
    """Drop everything but the newest `RETENTION_ROWS` rows."""
    db.execute(
        "delete from queries where id <= coalesce("
        "(select id from queries order by id desc limit 1 offset ?), 0)",
        (RETENTION_ROWS,),
    )
    db.commit()


class PermissiveEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        # Never raise a TypeError, just use the repr
        try:
            return str(obj)
        except TypeError:
            return ""


def get_connection_hash(
    conn_str: Sequence[str], config: Mapping[str, Any], *, through: Sequence[str] = ()
) -> str:
    """What a connection's cached catalog and query history are keyed by.

    `through` is how it was reached, where that is not part of the details
    themselves: two SSH tunnels front two databases that both look like
    `localhost:15439`. Absent from the hashed material when there is none, so
    an untunneled connection keys on its details alone.
    """
    material: dict[str, Any] = {"conn_str": tuple(conn_str), **config}
    if through:
        material["through"] = tuple(through)
    return (
        hashlib.md5(json.dumps(material, cls=PermissiveEncoder).encode("utf-8"))
        .digest()
        .hex()
    )


def connection_id(
    declared: str | None,
    conn_str: Sequence[str],
    options: Mapping[str, Any],
    *,
    through: Sequence[str] = (),
) -> str:
    """The id this connection is logged and cached under, in either command.

    `declared` is what the adapter says its connection is, where it says
    anything; the details it was built from are the fallback.
    """
    if declared is None:
        keyed = get_connection_hash(conn_str, options)
    elif isinstance(declared, str):
        # an adapter may hand back a hydrated connection string, and this
        # reaches a column. Deterministic, so the cache and the log agree.
        keyed = redact_conn_str([declared])[0]
    else:
        # adapters are third-party code, and this one did not return a string
        keyed = declared
    if through:
        return get_connection_hash((keyed,), {}, through=through)
    return keyed
