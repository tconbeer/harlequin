from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from platformdirs import user_cache_dir
from textual_textarea.key_handlers import Cursor as Cursor

from harlequin.catalog import Catalog

CACHE_VERSION = 0


class PermissiveEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        # Never raise a TypeError, just use the repr
        try:
            return str(obj)
        except TypeError:
            return ""


@dataclass
class CatalogCache:
    databases: dict[str, Catalog]


def get_connection_hash(conn_str: Sequence[str], config: dict[str, Any]) -> str:
    return (
        hashlib.md5(
            json.dumps(
                {"conn_str": tuple(conn_str), **config},
                cls=PermissiveEncoder,
            ).encode("utf-8")
        )
        .digest()
        .hex()
    )


def get_cached_catalog(connection_hash: str) -> Catalog | None:
    cache = _load_cache()
    if cache is None:
        return None
    return cache.databases.get(connection_hash, None)


def update_cache_with_catalog(connection_hash: str, catalog: Catalog) -> None:
    cache = _load_cache()
    if cache is None:
        cache = CatalogCache(databases={})
    cache.databases[connection_hash] = catalog
    _write_cache(cache)


def _get_cache_file() -> Path:
    """
    Returns the path to the cache file on disk
    """
    cache_dir = Path(user_cache_dir(appname="harlequin"))
    cache_file = cache_dir / f"catalog-cache-{CACHE_VERSION}.pickle"
    return cache_file


def _load_cache() -> CatalogCache | None:
    """
    Returns a Cache by loading from a pickle saved to disk
    """
    cache_file = _get_cache_file()
    try:
        with cache_file.open("rb") as f:
            cache: CatalogCache = pickle.load(f)
            assert isinstance(cache, CatalogCache)
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


def _write_cache(cache: CatalogCache) -> None:
    """
    Updates cache with current data catalog
    """
    cache_file = _get_cache_file()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(cache, f)
