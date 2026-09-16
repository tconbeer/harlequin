"""One SQLite store of every query both commands run, written as it runs.

`QueryLog` writes it and `recent()` reads it, and both front ends do both.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, cast, get_args

from platformdirs import user_state_path

from harlequin.redact import redact_conn_str, redact_sql

Status = Literal["ok", "error", "canceled"]
"""What became of one statement. Only the caller that cancelled a statement can
tell it from one that matched nothing, so `canceled` is its own status."""

STATUSES: tuple[str, ...] = get_args(Status)
"""Every status, for a reader offering them to filter by."""

PROGRAMS: tuple[str, ...] = ("harlequin", "hsql")
"""The commands that write to the store. `program` is a plain text column and
anything could write another, but these are the two this package ships; pinned
to what they pass by `test_every_program_that_writes_is_offered`."""


@dataclass(frozen=True)
class Record:
    """One statement to insert into the store from outside a run."""

    sql: str
    run_at: datetime
    status: Status = "ok"
    rows: int | None = None
    elapsed_ms: float | None = None


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
    (
        """
        create table migrated_connections (
          connection   text primary key,
          migrated_at  text    not null,
          records      integer not null
        )
        """,
    ),
)
"""Each entry takes `pragma user_version` from its index to the next, and is a
list of statements so that a migration runs inside one transaction."""

SCHEMA_VERSION = len(MIGRATIONS)

RETENTION_ROWS = 100_000
"""How many of the newest rows survive. Trimmed once per process."""

_trimmed: set[Path] = set()
_trimmed_lock = threading.Lock()
"""The stores this process has trimmed. A warm session opens a log per request,
and the walk a trim costs grows with the store -- 10ms of it at the cap -- so a
session would pay for retention on every query it answered."""

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

READ_COLUMNS = (
    "run_at",
    "program",
    "profile",
    "adapter",
    "status",
    "rows",
    "elapsed_ms",
    "sql",
)
"""What a read of the store returns, in the order `recent()` selects them.

Fewer than a writer supplies: `connection` is what a read filters *by* rather
than something to report, and `truncated` and `error` describe the run that
wrote a row rather than the query it ran.
"""

_SELECT = "select {} from queries".format(
    ", ".join(f'"{column}"' for column in READ_COLUMNS)
)

_LIKE_ESCAPE = "\\"
"""What `_like_literal()` puts in front of a wildcard, and the `escape` clause
below names. Not a SQLite escape itself: `'\\'` in a SQLite string literal is
one backslash."""

_SEARCH_CLAUSE = f"\"sql\" like ? escape '{_LIKE_ESCAPE}'"

ELAPSED_PLACES = 3
"""Decimal places kept on `elapsed_ms`: microseconds, which is finer than any
statement is and short enough to read in a listing. Rounded once, on the way
in, because the value is written once and printed every time it is read."""

BUSY_TIMEOUT_MS = 5000
"""How long a writer waits for a lock another process holds."""

UI_BUSY_TIMEOUT_MS = 250
"""What a front end waits, so a worker is not held on a lock another process
owns. A missed row beats a stalled worker."""


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
            _rounded(elapsed_ms),
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
                _rounded(elapsed_ms),
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
            # not thread-bound: the lock in `_run()` is what serializes writers
            db = sqlite3.connect(store, check_same_thread=False)
            _configure(db, busy_timeout_ms=self._busy_timeout_ms)
            _migrate(db)
            _trim_once(db, store)
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


def _rounded(elapsed_ms: float | None) -> float | None:
    """One statement's duration, at the precision the store keeps."""
    return None if elapsed_ms is None else round(elapsed_ms, ELAPSED_PLACES)


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


def _trim_once(db: sqlite3.Connection, store: Path) -> None:
    """Trim this store, unless this process already has.

    Retention is a soft cap, so trimming at the first open of a store is
    enough: what it bounds is a history kept for months.
    """
    with _trimmed_lock:
        if store in _trimmed:
            return
        _trim(db)
        _trimmed.add(store)


def _trim(db: sqlite3.Connection) -> None:
    """Drop everything but the newest `RETENTION_ROWS` rows."""
    db.execute(
        "delete from queries where id <= coalesce("
        "(select id from queries order by id desc limit 1 offset ?), 0)",
        (RETENTION_ROWS,),
    )
    db.commit()


def adopt(
    connection: str,
    records: Sequence[Record],
    *,
    program: str,
    path: Path | None = None,
    busy_timeout_ms: int = BUSY_TIMEOUT_MS,
) -> int | None:
    """Insert what a connection ran before it had a store, exactly once.

    The rows and the marker that says this connection has been adopted are one
    transaction, so two processes starting at once cannot both insert them, and
    a failure part-way leaves nothing behind for the next attempt to mistake
    for a finished one. Returns how many rows were written, or None if this
    connection had already been adopted.

    Raises: sqlite3.Error, OSError.
    """
    store = path if path is not None else default_path()
    store.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.close(os.open(store, os.O_CREAT | os.O_RDWR, 0o600))
    db = sqlite3.connect(store)
    try:
        _configure(db, busy_timeout_ms=busy_timeout_ms)
        _migrate(db)
        db.execute("begin immediate")
        try:
            already = db.execute(
                'select 1 from migrated_connections where "connection" = ?',
                (connection,),
            ).fetchone()
            if already is not None:
                db.rollback()
                return None
            db.executemany(
                _INSERT,
                [
                    _adopted_row(record, connection=connection, program=program)
                    for record in records
                ],
            )
            db.execute(
                "insert into migrated_connections "
                '("connection", "migrated_at", "records") values (?, ?, ?)',
                (connection, datetime.now(timezone.utc).isoformat(), len(records)),
            )
        except BaseException:
            db.rollback()
            raise
        db.commit()
    finally:
        db.close()
    return len(records)


def _adopted_row(record: Record, *, connection: str, program: str) -> tuple[Any, ...]:
    """One record as a row of `COLUMNS`, redacted as a live write would be."""
    return (
        record.run_at.isoformat(),
        program,
        connection,
        None,
        None,
        redact_sql(record.sql),
        record.status,
        record.rows,
        None,
        _rounded(record.elapsed_ms),
        None,
    )


def recent(
    *,
    connection: str | None = None,
    search: str | None = None,
    program: str | None = None,
    status: str | None = None,
    limit: int | None = None,
    path: Path | None = None,
    busy_timeout_ms: int = BUSY_TIMEOUT_MS,
) -> list[tuple[Any, ...]]:
    """The newest rows of the store first, as `READ_COLUMNS` describes them.

    One indexed read -- a filter on `connection`, a `like` on `sql`, an
    equality on `program` or `status`, and a limit -- so asking for twenty
    costs the same on a store of a hundred thousand rows as on a store of
    twenty. `limit` is a number of rows, or None for all of them.

    A store that is not there yet is a history of nothing rather than an error,
    and nothing here creates or migrates one: a reader that wrote would be a
    reader that could fail.

    Raises: sqlite3.Error, for a store that is there and cannot be read.
    """
    store = path if path is not None else default_path()
    if not store.exists():
        return []
    db = sqlite3.connect(store)
    try:
        db.execute(f"pragma busy_timeout = {int(busy_timeout_ms)}")
        (version,) = db.execute("pragma user_version").fetchone()
        if version < 1:
            # the file is there and the table is not, which is what a store an
            # opener created and then could not migrate looks like
            return []
        clauses = []
        values: list[Any] = []
        if connection is not None:
            clauses.append('"connection" = ?')
            values.append(connection)
        if search:
            clauses.append(_SEARCH_CLAUSE)
            values.append(f"%{_like_literal(search)}%")
        if program is not None:
            clauses.append('"program" = ?')
            values.append(program)
        if status is not None:
            clauses.append('"status" = ?')
            values.append(status)
        where = f" where {' and '.join(clauses)}" if clauses else ""
        # -1 is SQLite's own spelling of no limit, so one query shape serves
        # both, and `id desc` is the index the store was built with
        values.append(-1 if limit is None else limit)
        rows = db.execute(
            f'{_SELECT}{where} order by "id" desc limit ?', values
        ).fetchall()
    finally:
        db.close()
    return cast("list[tuple[Any, ...]]", rows)


def _like_literal(term: str) -> str:
    """One search term as `like` matches it character for character.

    `_` is in half the table names there are, and unescaped it matches any
    character: a search for `line_items` that also found `lineXitems` would be
    a filter nobody typed.
    """
    for character in (_LIKE_ESCAPE, "%", "_"):
        term = term.replace(character, _LIKE_ESCAPE + character)
    return term


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
