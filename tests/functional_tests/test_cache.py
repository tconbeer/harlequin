import pickle
from pathlib import Path
from typing import List

import pytest
from textual.widgets.text_area import Selection

from harlequin import Harlequin
from harlequin.editor_cache import (
    BufferState,
    Cache,
    get_cache_file,
    get_recovery_file,
    load_cache,
    write_cache,
)


@pytest.fixture
def buffer_states() -> List[BufferState]:
    return [
        BufferState(
            selection=Selection((0, 3), (0, 3)),
            text="select 1\n",
        ),
        BufferState(
            selection=Selection((0, 0), (0, 0)),
            text="",
        ),
        BufferState(
            selection=Selection((0, 0), (1, 0)),
            text="select\n*\nfrom\nfoo\n",
        ),
    ]


@pytest.fixture
def cache(buffer_states: List[BufferState]) -> Cache:
    return Cache(focus_index=1, buffers=buffer_states)


@pytest.fixture(autouse=True)
def mock_user_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr("harlequin.editor_cache.user_cache_dir", lambda **_: tmp_path)
    return tmp_path


@pytest.mark.use_cache
def test_cache_ops(mock_user_cache_dir: Path, cache: Cache) -> None:
    assert mock_user_cache_dir.exists()
    assert len(list(mock_user_cache_dir.iterdir())) == 0
    write_cache(cache)
    children = list(mock_user_cache_dir.iterdir())
    assert len(children) == 1
    assert children[0].suffix == ".pickle"
    assert get_cache_file() == children[0]
    loaded_cache = load_cache()
    assert loaded_cache == cache


@pytest.mark.use_cache
@pytest.mark.asyncio
async def test_harlequin_loads_cache(cache: Cache, app: Harlequin) -> None:
    write_cache(cache)
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        assert app.editor_collection is not None
        assert app.editor is not None
        assert app.editor_collection.tab_count == len(cache.buffers)
        assert [buffer.text for buffer in app.editor_collection.buffers] == [
            buffer.text for buffer in cache.buffers
        ]
        # the buffer that was active when the cache was written is active again
        assert app.editor_collection.active_buffer_index == cache.focus_index
        assert app.editor.text == cache.buffers[cache.focus_index].text


@pytest.mark.use_cache
@pytest.mark.asyncio
async def test_harlequin_writes_cache(app: Harlequin) -> None:
    cache_path = get_cache_file()
    assert not cache_path.exists()
    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        assert app.editor_collection is not None
        assert app.editor_collection.tab_count == 1
        app.editor.text = "first"
        await pilot.press("ctrl+n")
        await pilot.pause()
        app.editor.text = "second"
        await pilot.press("ctrl+q")
    assert cache_path.exists()
    with open(cache_path, "rb") as f:
        cache = pickle.load(f)
    assert isinstance(cache, Cache)
    assert [buffer.text for buffer in cache.buffers] == ["first", "second"]
    assert cache.focus_index == 1


@pytest.mark.use_cache
@pytest.mark.asyncio
async def test_harlequin_recovers_buffers(cache: Cache, app: Harlequin) -> None:
    """A recovered session started from the cache, so it wins over one."""
    write_cache(Cache(focus_index=0, buffers=[BufferState(Selection(), "stale\n")]))
    recovery_file = get_cache_file().with_name("recovered-20260904T120000Z-99.pickle")
    recovery_file.write_bytes(pickle.dumps(cache))

    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        assert app.editor_collection is not None
        assert [buffer.text for buffer in app.editor_collection.buffers] == [
            buffer.text for buffer in cache.buffers
        ]

    # the poisoned-buffer guard: replayed once, whatever happened during the replay
    assert not recovery_file.exists()
    assert recovery_file.with_suffix(".replayed").exists()


@pytest.mark.use_cache
@pytest.mark.asyncio
async def test_harlequin_clears_its_recovery_file_on_quit(app: Harlequin) -> None:
    recovery_file = get_recovery_file()
    recovery_file.parent.mkdir(parents=True, exist_ok=True)
    recovery_file.write_bytes(pickle.dumps(Cache(focus_index=0, buffers=[])))

    async with app.run_test() as pilot:
        while app.editor is None:
            await pilot.pause()
        await pilot.press("ctrl+q")

    assert not recovery_file.exists()
    assert get_cache_file().exists()
