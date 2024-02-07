import pickle
from pathlib import Path
from typing import List

import pytest
from harlequin import Harlequin
from harlequin.editor_cache import (
    BufferState,
    Cache,
    get_cache_file,
    load_cache,
    write_cache,
)
from textual.widgets.text_area import Selection


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
        assert [editor.text for editor in app.editor_collection.all_editors] == [
            buffer.text for buffer in cache.buffers
        ]


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
