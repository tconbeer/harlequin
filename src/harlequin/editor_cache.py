import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

from platformdirs import user_cache_dir
from textual.widgets.text_area import Selection

CACHE_VERSION = 1


@dataclass
class BufferState:
    selection: Selection
    text: str


@dataclass
class Cache:
    focus_index: int  # currently doesn't impact focus on load
    buffers: List[BufferState]


def get_cache_file() -> Path:
    """
    Returns the path to the cache file on disk
    """
    cache_dir = Path(user_cache_dir(appname="harlequin"))
    cache_file = cache_dir / f"cache-{CACHE_VERSION}.pickle"
    return cache_file


def load_cache() -> Union[Cache, None]:
    """
    Returns a Cache (a list of strings) by loading
    from a pickle saved to disk
    """
    cache_file = get_cache_file()
    try:
        with cache_file.open("rb") as f:
            cache: Cache = pickle.load(f)
            assert isinstance(cache, Cache)
    except (
        pickle.UnpicklingError,
        ValueError,
        IndexError,
        FileNotFoundError,
        AssertionError,
    ):
        return None
    else:
        return cache


def write_cache(cache: Cache) -> None:
    """
    Updates dumps buffer contents to to disk
    """
    cache_file = get_cache_file()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(cache, f)
