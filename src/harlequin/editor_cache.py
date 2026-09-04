from __future__ import annotations

import os
import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

from platformdirs import user_cache_dir
from textual.widgets.text_area import Selection

CACHE_VERSION = 1

CHECKPOINT_INTERVAL_SECONDS = 60.0
"""How often a running Harlequin writes its buffers to its recovery file."""

RECOVERY_STALE_SECONDS = 3 * CHECKPOINT_INTERVAL_SECONDS
"""How long a recovery file must go untouched before another process adopts it.

A live sibling checkpoints well inside this window, so its file is left alone
without a PID-liveness check, which would be wrong on Windows and wrong under
PID reuse.
"""


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
    return _get_cache_dir() / f"cache-{CACHE_VERSION}.pickle"


def get_recovery_file() -> Path:
    """
    Returns the path to this process's recovery file on disk.

    One file per process, so two Harlequins keep the shared cache's
    comprehensible "last clean quit wins" semantics instead of thrashing it,
    and a checkpoint can never overwrite what another process wrote on its way
    out.
    """
    return _get_cache_dir() / f"recovery-{os.getpid()}.pickle"


def _get_cache_dir() -> Path:
    return Path(user_cache_dir(appname="harlequin"))


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


def write_recovery(cache: Cache) -> bool:
    """
    Checkpoints buffer contents. Returns False if they could not be written.
    """
    return _write_pickle(cache, get_recovery_file())


def clear_recovery() -> None:
    """
    Drops this process's recovery file, after a clean quit saved the cache.
    """
    try:
        get_recovery_file().unlink(missing_ok=True)
    except OSError:
        pass


def adopt_recovery() -> tuple[Union[Cache, None], Union[Path, None]]:
    """
    Takes over the buffers of a session that ended unexpectedly, if there are any.

    The chosen file is renamed to `.replayed` *before* it is unpickled, so a
    crash while replaying it cannot repeat: the next start finds nothing to
    take over. That is the whole crash-loop guard, and it is correct across
    concurrent processes by construction.
    """
    for candidate in _recovery_candidates():
        replayed = candidate.with_suffix(".replayed")
        try:
            os.replace(candidate, replayed)
        except OSError:
            continue
        try:
            with replayed.open("rb") as f:
                cache = pickle.load(f)
            assert isinstance(cache, Cache)
        except Exception:
            continue
        return cache, replayed
    return None, None


def _recovery_candidates() -> List[Path]:
    """
    Recovery files this process may adopt, most recently written first.

    A crash handler's `recovered-*` file is always adopted; a `recovery-*` file
    is only adopted once it has gone stale, which is how a sibling process that
    is still running keeps its own.
    """
    cache_dir = _get_cache_dir()
    stale_before = time.time() - RECOVERY_STALE_SECONDS
    candidates: List[Path] = []
    try:
        for pattern, cutoff in (
            ("recovered-*.pickle", None),
            ("recovery-*.pickle", stale_before),
        ):
            matches = []
            for path in cache_dir.glob(pattern):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if cutoff is None or mtime < cutoff:
                    matches.append((mtime, path))
            candidates.extend(path for _, path in sorted(matches, reverse=True))
    except OSError:
        return []
    return candidates
