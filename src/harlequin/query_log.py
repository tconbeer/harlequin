"""Every query both commands have run, in one SQLite database.

One store, at `<user_state_dir>/harlequin/history.db`, one row per statement,
written as each statement runs -- so a session that never exits cleanly still
has its history, and an agent's queries and a human's are one list rather than
two.

**SQLite because the store is searched.** A tail read is bounded by
construction, which an append-only file serves well; a search reaches queries
run weeks ago, which means reading all of them. Over 100,000 records that is
396ms as a file scan and 35ms as a `like`, and 400ms per keystroke is not a
filter anyone can type into. The pragmas below are part of that answer rather
than tuning, and a schema change here is an `alter table`: this is the one
store whose contents cannot be re-fetched, so a version bump may not discard
what it does not understand.

**Logging never fails a query.** A store that cannot be opened, migrated or
written disables itself and records why, in one place instead of at every call
site; the caller says so once and runs on. The first write is what opens it, so
holding a log costs nothing until there is something to record.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from platformdirs import user_state_path

from harlequin.redact import redact_text

Status = Literal["ok", "error", "canceled"]
"""What became of one statement.

`canceled` is a status of its own because a cancelled cursor comes back empty
and error-free, exactly like a query that matched nothing: only the caller that
cancelled it can tell them apart.
"""

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
"""Each entry takes `pragma user_version` from its index to the next.

Append-only, and statements rather than a script so that a migration runs
inside the transaction that claimed the right to run it.
"""

SCHEMA_VERSION = len(MIGRATIONS)

RETENTION_ROWS = 100_000
"""How many of the newest rows survive. Trimmed once per process, and as a
delete rather than a rotation, so nothing is lost from the end that the
beginning did not push out."""

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

_BUSY_TIMEOUT_MS = 5000
"""Long enough that several processes writing at once is a non-event: eight of
them, 200 inserts each, produced 1,600 rows and no errors in 0.15s."""


def default_path() -> Path:
    """Where the store lives, for a caller that was given no path.

    State rather than cache: a cache is a thing that can be thrown away and
    re-fetched, and a query history is neither.
    """
    return user_state_path(appname="harlequin") / "history.db"


class QueryLog:
    """The store, open for one command's run.

    What every row of a run shares -- which program, which connection, which
    profile -- is held here rather than passed to each write, because none of
    it can change while a process is running.

    A disabled log is a real object whose writes do nothing, so that a caller
    never branches on None: `--no-write-history`, and a store that could not be
    opened, are the same object to everything upstream.

    **Constructing one touches no disk.** The store is opened, migrated and
    trimmed by the first write, so a front end can hold one from the moment it
    starts and a session that runs no queries pays nothing for it.
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
    ) -> None:
        self.program = program
        self.connection = connection
        self.profile = profile
        self.adapter = adapter
        self.failure: str | None = None
        """Why nothing is being logged, for a caller to report once."""
        self._path = path
        self._enabled = enabled
        self._db: sqlite3.Connection | None = None

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
            # a query can carry a credential -- `attach 'postgres://u:pw@h'` --
            # and this store outlives the process that wrote it
            redact_text(sql),
            status,
            rows,
            None if truncated is None else int(truncated),
            elapsed_ms,
            None if error is None else redact_text(error),
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
        """Complete the row for a statement whose result is now known.

        Two phases because the row is written when the statement runs and the
        rows it returned are known only after they are fetched -- and a run
        that dies in between has still recorded the query.
        """
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
                None if error is None else redact_text(error),
                row,
            ),
        )

    def close(self) -> None:
        """Let go of the store.

        Writes after this are the no-ops a disabled log's are.
        """
        if self._db is not None:
            with contextlib.suppress(sqlite3.Error):
                self._db.close()
            self._db = None
        self._enabled = False

    def _open(self) -> sqlite3.Connection | None:
        """The store, configured and migrated, or None having recorded why.

        Called by the first write rather than by `__init__`, so that opening a
        file, migrating it and trimming it is work a session pays for when it
        runs a query and not while it is starting up.
        """
        store = self._path if self._path is not None else default_path()
        db: sqlite3.Connection | None = None
        try:
            store.parent.mkdir(parents=True, exist_ok=True)
            # not thread-bound: hsql writes from the worker thread `--timeout`
            # moves a run to, and one connection is only ever written by one
            # thread at a time in either command.
            db = sqlite3.connect(store, check_same_thread=False)
            _configure(db)
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

        The single place a write can fail -- opening it included, since that is
        what the first one does. The query being recorded has already run, so
        there is nothing here worth raising over.
        """
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
            self.close()
            return None


def _configure(db: sqlite3.Connection) -> None:
    """The pragmas that make a write 0.02ms and a concurrent one a non-event.

    WAL wants shared memory, which a network home directory does not have, so a
    refusal falls back to the default journal -- 34x slower per write, and just
    as correct.
    """
    db.execute(f"pragma busy_timeout = {_BUSY_TIMEOUT_MS}")
    with contextlib.suppress(sqlite3.Error):
        db.execute("pragma journal_mode = wal")
    db.execute("pragma synchronous = normal")


def _migrate(db: sqlite3.Connection) -> None:
    """Bring the store up to `SCHEMA_VERSION`, one migration at a time.

    Under `begin immediate`, so that two processes opening a new store at once
    do not both create it. A store written by a newer Harlequin is left alone:
    its columns are a superset of these, so the inserts still name what they
    mean.
    """
    db.execute("begin immediate")
    try:
        (version,) = db.execute("pragma user_version").fetchone()
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

    Here rather than in `catalog_cache` because a headless caller keys the same
    connection the same way, and that module renders a history with rich.
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
    anything; the details it was built from are the fallback. Shared because
    the two commands writing one store have to agree about which database a row
    belongs to.
    """
    keyed = declared if declared is not None else get_connection_hash(conn_str, options)
    if through:
        return get_connection_hash((keyed,), {}, through=through)
    return keyed
