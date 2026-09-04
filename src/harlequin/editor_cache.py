import os
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
    focus_index: int
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


def _write_pickle(cache: Cache, path: Path) -> bool:
    """
    Pickles a Cache to path, atomically. Returns False if it could not be written.

    The write goes to a temp file in the same directory and is renamed into
    place, so a reader never sees a partial file; the fsync is what keeps a
    power loss from leaving a zero-length one. The temp name carries the pid,
    so a killed process leaves at most one stray behind. It never raises: the
    callers run where an exception would itself take the app down.
    """
    temp_file = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_file, "wb") as f:
            pickle.dump(cache, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, path)
    except (OSError, pickle.PicklingError):
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    else:
        return True


def write_cache(cache: Cache) -> bool:
    """
    Dumps buffer contents to disk. Returns False if they could not be written.
    """
    return _write_pickle(cache, get_cache_file())
