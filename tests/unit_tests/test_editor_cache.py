from __future__ import annotations

import pickle
from pathlib import Path

import pytest
from textual.widgets.text_area import Selection

from harlequin.editor_cache import (
    BufferState,
    Cache,
    get_cache_file,
    load_cache,
    write_cache,
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
