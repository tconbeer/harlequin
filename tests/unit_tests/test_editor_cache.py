from __future__ import annotations

import os
import pickle
import sqlite3
import time
from pathlib import Path

import pytest
from textual.geometry import Offset
from textual.widgets.text_area import Selection

from harlequin.editor_cache import (
    CLOSED_BUFFER_LIMIT,
    RECOVERY_STALE_SECONDS,
    BufferState,
    Cache,
    _read,
    adopt_recovery,
    clear_recovery,
    get_cache_file,
    get_recovery_file,
    load_cache,
    write_cache,
    write_recovery,
)


@pytest.fixture
def cache() -> Cache:
    return Cache(
        focus_index=0,
        buffers=[BufferState(selection=Selection((0, 3), (0, 3)), text="select 1\n")],
    )


@pytest.fixture(autouse=True)
def mock_user_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr("harlequin.editor_cache.user_cache_dir", lambda **_: cache_dir)
    monkeypatch.setattr("harlequin.editor_cache.user_state_path", lambda **_: cache_dir)
    return cache_dir


@pytest.fixture
def failing_store(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("no")

    monkeypatch.setattr("harlequin.editor_cache._store", fail)


def test_write_cache_round_trips(mock_user_cache_dir: Path, cache: Cache) -> None:
    assert write_cache(cache) is True
    assert load_cache() == cache


def test_closed_buffers_round_trip_in_closing_order(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    cache.closed_buffers = [
        BufferState(Selection((0, 2), (0, 5)), "select 2"),
        BufferState(Selection(), "select 3"),
    ]
    assert write_cache(cache)
    assert load_cache() == cache
    assert write_recovery(cache)
    recovery = get_recovery_file()
    os.utime(recovery, (0, 0))
    assert adopt_recovery()[0] == cache


def legacy_pickle(cache: Cache) -> bytes:
    """Serialize the fields present in a version-one editor cache."""
    from copy import deepcopy

    legacy_cache = deepcopy(cache)
    del legacy_cache.closed_buffers
    for buffer in legacy_cache.buffers:
        del buffer.scroll_offset
    return pickle.dumps(legacy_cache)


def test_load_migrates_legacy_buffers_without_discarding_them(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    legacy_file = mock_user_cache_dir / "cache-1.pickle"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_bytes(legacy_pickle(cache))
    migrated = load_cache()
    assert migrated == Cache(focus_index=cache.focus_index, buffers=cache.buffers)
    assert get_cache_file().exists()
    assert not legacy_file.exists()
    assert load_cache() == migrated


@pytest.mark.usefixtures("failing_store")
def test_failed_migration_preserves_the_legacy_file(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    legacy_file = mock_user_cache_dir / "cache-1.pickle"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_bytes(legacy_pickle(cache))
    assert load_cache() == Cache(focus_index=cache.focus_index, buffers=cache.buffers)
    assert legacy_file.exists()


def test_a_current_cache_wins_over_a_stale_legacy_cache(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    assert write_cache(Cache(focus_index=0, buffers=[]))
    legacy_file = mock_user_cache_dir / "cache-1.pickle"
    legacy_file.write_bytes(legacy_pickle(cache))
    assert load_cache() == Cache(focus_index=0, buffers=[])


def test_legacy_recovery_is_normalized(mock_user_cache_dir: Path, cache: Cache) -> None:
    legacy_file = mock_user_cache_dir / "recovered-legacy.pickle"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_bytes(legacy_pickle(cache))
    assert adopt_recovery()[0] == Cache(
        focus_index=cache.focus_index, buffers=cache.buffers
    )


def test_write_cache_leaves_no_temp_file(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    assert write_cache(cache) is True
    assert [path.name for path in mock_user_cache_dir.iterdir()] == [
        get_cache_file().name
    ]


def test_a_failed_write_leaves_the_last_cache_alone(
    monkeypatch: pytest.MonkeyPatch, cache: Cache
) -> None:
    assert write_cache(cache)

    def fail_after_delete(db: sqlite3.Connection, *_: object) -> None:
        db.execute("delete from editor_sessions")
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr("harlequin.editor_cache._store", fail_after_delete)
    assert not write_cache(Cache(focus_index=0, buffers=[]))
    assert load_cache() == cache


def test_write_cache_returns_false_when_the_cache_dir_is_unwritable(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    mock_user_cache_dir.parent.mkdir(parents=True, exist_ok=True)
    mock_user_cache_dir.write_text("not a directory")
    assert write_cache(cache) is False


def test_write_recovery_does_not_touch_the_shared_cache(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    assert write_cache(cache) is True
    before = get_cache_file().read_bytes()

    assert write_recovery(Cache(focus_index=0, buffers=[])) is True

    assert get_recovery_file().exists()
    assert get_recovery_file() != get_cache_file()
    assert get_cache_file().read_bytes() == before

    clear_recovery()
    assert not get_recovery_file().exists()
    assert get_cache_file().read_bytes() == before


def test_clear_recovery_when_there_is_nothing_to_clear(
    mock_user_cache_dir: Path,
) -> None:
    clear_recovery()


def test_adopt_recovery_finds_nothing(mock_user_cache_dir: Path, cache: Cache) -> None:
    assert write_cache(cache) is True
    assert adopt_recovery() == (None, None)


def test_adopt_recovery_replays_a_crash_handlers_file(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    planted = _plant(mock_user_cache_dir, "recovered-20260904T120000Z-99.pickle", cache)

    recovered, replayed = adopt_recovery()

    assert recovered == cache
    assert replayed == planted.with_suffix(".replayed")
    assert not planted.exists()


def test_adopt_recovery_replays_each_file_once(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    """The crash-loop guard: a file is renamed before it is unpickled."""
    _plant(mock_user_cache_dir, "recovered-20260904T120000Z-99.pickle", cache)

    assert adopt_recovery()[0] == cache
    assert adopt_recovery() == (None, None)


def test_adopt_recovery_consumes_a_file_it_cannot_unpickle(
    mock_user_cache_dir: Path,
) -> None:
    """A file that poisons the replay is spent by the attempt, not left to repeat."""
    planted = mock_user_cache_dir / "recovered-20260904T120000Z-99.pickle"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_bytes(b"not a pickle")

    assert adopt_recovery() == (None, None)

    assert not planted.exists()
    assert planted.with_suffix(".replayed").exists()


def test_adopt_recovery_leaves_a_live_sessions_file_alone(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    _plant(mock_user_cache_dir, "recovery-99.pickle", cache)

    assert adopt_recovery() == (None, None)

    assert (mock_user_cache_dir / "recovery-99.pickle").exists()


def test_adopt_recovery_takes_over_a_stale_file(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    planted = _plant(
        mock_user_cache_dir,
        "recovery-99.pickle",
        cache,
        age_seconds=RECOVERY_STALE_SECONDS + 1,
    )

    recovered, replayed = adopt_recovery()

    assert recovered == cache
    assert replayed == planted.with_suffix(".replayed")


def test_adopt_recovery_prefers_the_newest_crash(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    newest = Cache(focus_index=0, buffers=[BufferState(Selection(), "select 2\n")])
    _plant(
        mock_user_cache_dir,
        "recovered-20260904T120000Z-98.pickle",
        cache,
        age_seconds=60,
    )
    _plant(mock_user_cache_dir, "recovered-20260904T130000Z-99.pickle", newest)
    _plant(
        mock_user_cache_dir,
        "recovery-97.pickle",
        cache,
        age_seconds=RECOVERY_STALE_SECONDS + 1,
    )

    assert adopt_recovery()[0] == newest


def _plant(cache_dir: Path, name: str, cache: Cache, age_seconds: float = 0.0) -> Path:
    """Write a recovery file as another process would have left it."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / name
    path.write_bytes(pickle.dumps(cache))
    if age_seconds:
        mtime = time.time() - age_seconds
        os.utime(path, (mtime, mtime))
    return path


def test_saved_buffers_are_scoped_to_the_connection(cache: Cache) -> None:
    other = Cache(0, [BufferState(Selection(), "other database")])
    assert write_cache(cache, "one")
    assert write_cache(other, "two")
    assert load_cache("one") == cache
    assert load_cache("two") == other
    assert load_cache("three") is None
    assert write_cache(Cache(0, []), "one")
    assert load_cache("two") == other


def test_scroll_and_multibyte_text_round_trip(cache: Cache) -> None:
    cache.buffers[0].text = "客户\nselect '😀';"
    cache.buffers[0].selection = Selection((0, 1), (1, 8))
    cache.buffers[0].scroll_offset = Offset(20, 30)
    cache.closed_buffers = list(cache.buffers)
    assert write_cache(cache)
    assert load_cache() == cache


def test_retention_keeps_the_newest_closed_buffers_and_all_open_buffers() -> None:
    buffers = [
        BufferState(Selection(), str(index)) for index in range(CLOSED_BUFFER_LIMIT + 2)
    ]
    assert write_cache(Cache(1, buffers, buffers), "one")
    restored = load_cache("one")
    assert restored is not None
    assert restored.buffers == buffers
    assert restored.closed_buffers == buffers[2:]
    assert write_recovery(Cache(1, buffers, buffers), "one")
    checkpoint = _read(get_recovery_file("one"), "one")
    assert checkpoint == restored


def test_recovery_is_scoped_and_does_not_consume_another_connection(
    cache: Cache,
) -> None:
    assert write_recovery(cache, "one")
    checkpoint = get_recovery_file("one")
    os.utime(checkpoint, (0, 0))
    assert adopt_recovery("two") == (None, None)
    assert checkpoint.exists()
    assert adopt_recovery("one")[0] == cache
    assert adopt_recovery("one") == (None, None)


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_cache_is_claimed_once(
    mock_user_cache_dir: Path,
    cache: Cache,
    version: int,
) -> None:
    cache.closed_buffers = [BufferState(Selection((0, 1), (0, 2)), "closed")]
    legacy_file = mock_user_cache_dir / f"cache-{version}.pickle"
    legacy_file.parent.mkdir(parents=True)
    if version == 1:
        content = legacy_pickle(cache)
    else:
        content = pickle.dumps(cache)
    legacy_file.write_bytes(content)
    restored = load_cache("one")
    assert restored is not None
    assert restored.buffers == cache.buffers
    assert restored.closed_buffers == ([] if version == 1 else cache.closed_buffers)
    # Even an undeletable legacy file cannot be imported into a second connection.
    legacy_file.write_bytes(content)
    assert load_cache("two") is None
    assert load_cache("one") == restored


def test_a_locked_store_preserves_the_saved_snapshot(cache: Cache) -> None:
    assert write_cache(cache)
    with sqlite3.connect(get_cache_file()) as db:
        db.execute("begin immediate")
        assert not write_cache(Cache(0, []))
    assert load_cache() == cache


def test_corrupt_store_does_not_crash_startup(mock_user_cache_dir: Path) -> None:
    mock_user_cache_dir.mkdir()
    get_cache_file().write_bytes(b"not sqlite")
    assert load_cache() is None
    assert not write_cache(Cache(0, []))
