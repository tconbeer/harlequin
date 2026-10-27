"""Connection-scoped editor buffers and crash recovery, stored in SQLite."""

from __future__ import annotations

import hashlib
import os
import pickle
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_cache_dir, user_state_path
from textual.geometry import Offset
from textual.widgets.text_area import Selection

CLOSED_BUFFER_LIMIT = 1000
"""The newest closed buffers retained per connection."""

CHECKPOINT_INTERVAL_SECONDS = 60.0
RECOVERY_STALE_SECONDS = 3 * CHECKPOINT_INTERVAL_SECONDS


@dataclass
class BufferState:
    selection: Selection
    text: str
    scroll_offset: Offset = field(default_factory=Offset)


@dataclass
class Cache:
    focus_index: int
    buffers: list[BufferState]
    closed_buffers: list[BufferState] = field(default_factory=list)


def get_cache_file() -> Path:
    """The SQLite store shared by cleanly closed editor sessions."""
    return user_state_path(appname="harlequin") / "buffers.db"


def _connection_key(connection: str) -> str:
    return hashlib.sha256(connection.encode()).hexdigest()


def get_recovery_file(connection: str = "") -> Path:
    """This process's checkpoint, isolated from other connections and processes."""
    return get_cache_file().with_name(
        f"recovery-{_connection_key(connection)}-{os.getpid()}.db"
    )


def _open(path: Path) -> sqlite3.Connection:
    """Open a private store with bounded lock waiting and foreign keys."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    db = sqlite3.connect(path, timeout=0.25)
    db.execute("pragma foreign_keys = on")
    return db


def _create_tables(db: sqlite3.Connection) -> None:
    db.execute(
        "create table if not exists editor_sessions ("
        "connection text primary key, focus_index integer not null)"
    )
    db.execute(
        "create table if not exists editor_buffers ("
        "connection text not null references editor_sessions(connection) "
        "on delete cascade,"
        "closed integer not null, position integer not null, text text not null,"
        "start_row integer not null, start_column integer not null,"
        "end_row integer not null, end_column integer not null,"
        "scroll_x integer not null, scroll_y integer not null,"
        "primary key (connection, closed, position))"
    )
    db.execute("create table if not exists editor_migrations (name text primary key)")


def _store(db: sqlite3.Connection, cache: Cache, connection: str) -> None:
    db.execute("delete from editor_sessions where connection = ?", (connection,))
    db.execute(
        "insert into editor_sessions values (?, ?)", (connection, cache.focus_index)
    )
    db.executemany(
        "insert into editor_buffers values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                connection,
                closed,
                position,
                buffer.text,
                *buffer.selection.start,
                *buffer.selection.end,
                *buffer.scroll_offset,
            )
            for closed, buffers in (
                (0, cache.buffers),
                (1, cache.closed_buffers[-CLOSED_BUFFER_LIMIT:]),
            )
            for position, buffer in enumerate(buffers)
        ),
    )


def _write(cache: Cache, path: Path, connection: str) -> bool:
    """Replace one connection's snapshot atomically, preserving others."""
    try:
        with closing(_open(path)) as db, db:
            db.execute("begin immediate")
            _create_tables(db)
            _store(db, cache, connection)
    except (OSError, sqlite3.Error):
        return False
    return True


def _read(path: Path, connection: str) -> Cache | None:
    if not path.exists():
        return None
    try:
        with (
            closing(
                sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0.25)
            ) as db,
            db,
        ):
            db.execute("begin")
            session = db.execute(
                "select focus_index from editor_sessions where connection = ?",
                (connection,),
            ).fetchone()
            if session is None:
                return None
            cache = Cache(focus_index=session[0], buffers=[])
            for closed in (0, 1):
                buffers = cache.closed_buffers if closed else cache.buffers
                rows = db.execute(
                    "select text, start_row, start_column, end_row, end_column,"
                    "scroll_x, scroll_y from editor_buffers "
                    "where connection = ? and closed = ? order by position",
                    (connection, closed),
                )
                for (
                    text,
                    start_row,
                    start_column,
                    end_row,
                    end_column,
                    scroll_x,
                    scroll_y,
                ) in rows:
                    buffers.append(
                        BufferState(
                            text=text,
                            selection=Selection(
                                (start_row, start_column), (end_row, end_column)
                            ),
                            scroll_offset=Offset(scroll_x, scroll_y),
                        )
                    )
            cache.closed_buffers = cache.closed_buffers[-CLOSED_BUFFER_LIMIT:]
            return cache
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None


def _read_pickle(path: Path) -> Cache | None:
    """Normalize a legacy editor snapshot without changing its source file."""
    try:
        with path.open("rb") as stream:
            value = pickle.load(stream)
        if not isinstance(value, Cache):
            return None

        def normalize(buffer: BufferState) -> BufferState:
            return BufferState(
                selection=buffer.selection,
                text=buffer.text,
                scroll_offset=getattr(buffer, "scroll_offset", Offset()),
            )

        return Cache(
            focus_index=value.focus_index,
            buffers=[normalize(buffer) for buffer in value.buffers],
            closed_buffers=[
                normalize(buffer) for buffer in getattr(value, "closed_buffers", [])
            ][-CLOSED_BUFFER_LIMIT:],
        )
    except Exception:
        return None


def _migrate(connection: str) -> Cache | None:
    """Assign the unscoped legacy cache to the first connection that claims it."""
    cache_dir = Path(user_cache_dir(appname="harlequin"))
    paths = [cache_dir / f"cache-{version}.pickle" for version in (2, 1)]
    cache = next(
        (cache for path in paths if (cache := _read_pickle(path)) is not None), None
    )
    if cache is None:
        return None
    try:
        with closing(_open(get_cache_file())) as db, db:
            db.execute("begin immediate")
            _create_tables(db)
            if db.execute(
                "select 1 from editor_migrations where name = 'pickle'"
            ).fetchone():
                return None
            if not db.execute(
                "select 1 from editor_sessions where connection = ?", (connection,)
            ).fetchone():
                _store(db, cache, connection)
            db.execute("insert into editor_migrations values ('pickle')")
    except (OSError, sqlite3.Error):
        return cache
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return cache


def load_cache(connection: str = "") -> Cache | None:
    """Load this connection's buffers, migrating a legacy cache once."""
    migrated = _migrate(connection)
    return _read(get_cache_file(), connection) or migrated


def write_cache(cache: Cache, connection: str = "") -> bool:
    """Save this connection's clean session without replacing other connections."""
    return _write(cache, get_cache_file(), connection)


def write_recovery(cache: Cache, connection: str = "") -> bool:
    """Checkpoint this session without touching its last clean snapshot."""
    return _write(cache, get_recovery_file(connection), connection)


def clear_recovery(connection: str = "") -> None:
    """Drop this process's checkpoint after a successful clean save."""
    try:
        get_recovery_file(connection).unlink(missing_ok=True)
    except OSError:
        pass


def adopt_recovery(connection: str = "") -> tuple[Cache | None, Path | None]:
    """Claim a matching crashed session once, leaving live sessions alone."""
    for candidate in _recovery_candidates(connection):
        replayed = candidate.with_suffix(".replayed")
        try:
            os.replace(candidate, replayed)
        except OSError:
            continue
        if candidate.suffix == ".pickle":
            cache = _read_pickle(replayed)
        else:
            cache = _read(replayed, connection)
        if cache is not None:
            return cache, replayed
    return None, None


def _recovery_candidates(connection: str) -> list[Path]:
    """Crashes first, then stale checkpoints, newest first within each group."""
    key = _connection_key(connection)
    stale_before = time.time() - RECOVERY_STALE_SECONDS
    candidates: list[Path] = []
    for directory, crash_pattern, recovery_pattern in (
        (get_cache_file().parent, f"recovered-{key}-*.db", f"recovery-{key}-*.db"),
        (
            Path(user_cache_dir(appname="harlequin")),
            "recovered-*.pickle",
            "recovery-*.pickle",
        ),
    ):
        for pattern, cutoff in (
            (crash_pattern, None),
            (recovery_pattern, stale_before),
        ):
            matches = []
            for path in directory.glob(pattern):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if cutoff is None or mtime < cutoff:
                    matches.append((mtime, path))
            candidates.extend(path for _, path in sorted(matches, reverse=True))
    return candidates
