from __future__ import annotations

import os
import pickle
import time
from pathlib import Path

import pytest
from textual.widgets.text_area import Selection

from harlequin.editor_cache import (
    RECOVERY_STALE_SECONDS,
    BufferState,
    Cache,
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
    return cache_dir


@pytest.fixture
def failing_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    def _dump(*_args: object, **_kwargs: object) -> None:
        raise pickle.PicklingError("no")

    monkeypatch.setattr("harlequin.editor_cache.pickle.dump", _dump)


def test_write_cache_round_trips(mock_user_cache_dir: Path, cache: Cache) -> None:
    assert write_cache(cache) is True
    assert load_cache() == cache


def test_write_cache_leaves_no_temp_file(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    assert write_cache(cache) is True
    assert [path.name for path in mock_user_cache_dir.iterdir()] == [
        get_cache_file().name
    ]


@pytest.mark.usefixtures("failing_dump")
def test_a_failed_write_leaves_the_last_cache_alone(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    """The point of the temp file: a write that dies cannot destroy what is there."""
    good_cache = get_cache_file()
    good_cache.parent.mkdir(parents=True, exist_ok=True)
    good_cache.write_bytes(pickle.dumps(cache))
    before = good_cache.read_bytes()

    assert write_cache(Cache(focus_index=0, buffers=[])) is False

    assert good_cache.read_bytes() == before
    assert [path.name for path in mock_user_cache_dir.iterdir()] == [good_cache.name]


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
