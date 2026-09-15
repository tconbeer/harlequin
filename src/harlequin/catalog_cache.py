from __future__ import annotations

import contextlib
import pickle
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from platformdirs import user_cache_dir

from harlequin.catalog import Catalog
from harlequin.query_log import get_connection_hash  # re-exported

if TYPE_CHECKING:
    from harlequin.components.data_catalog import S3Tree
    from harlequin.history import QueryExecution

CACHE_VERSION = 3

HISTORY_CACHE_VERSION = 2
"""The version whose pickle holds a query history."""

__all__ = [
    "CatalogCache",
    "get_catalog_cache",
    "get_connection_hash",
    "load_legacy_history",
    "restrict_legacy_cache",
    "update_catalog_cache",
]


def recursive_dict() -> defaultdict:
    return defaultdict(recursive_dict)


@dataclass
class CatalogCache:
    databases: dict[str, Catalog]
    s3: dict[tuple[str | None, str | None, str | None], dict]

    def get_db(self, connection_hash: str) -> Catalog | None:
        # if connection_hash:
        #     return self.databases.get(connection_hash, None)
        return None

    def get_s3(
        self, cache_key: tuple[str | None, str | None, str | None]
    ) -> dict | None:
        return self.s3.get(cache_key, None)


def get_catalog_cache() -> CatalogCache | None:
    return _load_cache()


def update_catalog_cache(
    connection_hash: str | None,
    catalog: Catalog | None,
    s3_tree: S3Tree | None,
) -> None:
    if connection_hash is None and s3_tree is None:
        return
    cache = _load_cache()
    if cache is None:
        cache = CatalogCache(databases={}, s3={})
    # if catalog is not None and connection_hash:
    #     cache.databases[connection_hash] = catalog
    if s3_tree is not None and s3_tree.catalog_data is not None:
        cache.s3[s3_tree.cache_key] = s3_tree.catalog_data
    _write_cache(cache)


def load_legacy_history(connection_hash: str) -> list[QueryExecution] | None:
    """The queries a version-2 cache holds for one connection, if it holds any."""
    cache_file = _get_cache_file(HISTORY_CACHE_VERSION)
    if not cache_file.exists():
        return None
    cache = _load_cache(cache_file)
    if cache is None:
        return None
    # a version-2 pickle carries a history dict keyed by connection hash
    history = getattr(cache, "history", {}).get(connection_hash)
    return None if history is None else list(history)


def restrict_legacy_cache() -> None:
    """Take the group and world bits off a version-2 cache.

    It holds every statement it recorded, and nothing redacted them.
    """
    with contextlib.suppress(OSError):
        _get_cache_file(HISTORY_CACHE_VERSION).chmod(0o600)


def _get_cache_file(version: int = CACHE_VERSION) -> Path:
    """
    Returns the path to the cache file on disk
    """
    cache_dir = Path(user_cache_dir(appname="harlequin"))
    cache_file = cache_dir / f"catalog-cache-{version}.pickle"
    return cache_file


def _load_cache(cache_file: Path | None = None) -> CatalogCache | None:
    """
    Returns a Cache by loading from a pickle saved to disk
    """
    if cache_file is None:
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
        EOFError,
        # an older pickle names classes by module path, which this version may
        # have renamed or moved
        AttributeError,
        ImportError,
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
