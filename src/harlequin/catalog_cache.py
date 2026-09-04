from __future__ import annotations

import pickle
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from platformdirs import user_cache_dir

from harlequin.catalog import Catalog
from harlequin.history import History
from harlequin.query_log import get_connection_hash

if TYPE_CHECKING:
    from harlequin.components.data_catalog import S3Tree

CACHE_VERSION = 2

__all__ = [
    "CatalogCache",
    "get_catalog_cache",
    "get_connection_hash",
    "update_catalog_cache",
]
"""`get_connection_hash()` lives in `harlequin.query_log`, below the rich this
module renders a history with, and is re-exported for the callers that have
always asked this module which connection they are looking at."""


def recursive_dict() -> defaultdict:
    return defaultdict(recursive_dict)


@dataclass
class CatalogCache:
    databases: dict[str, Catalog]
    s3: dict[tuple[str | None, str | None, str | None], dict]
    history: dict[str, History]

    def get_db(self, connection_hash: str) -> Catalog | None:
        # if connection_hash:
        #     return self.databases.get(connection_hash, None)
        return None

    def get_history(self, connection_hash: str) -> History | None:
        if connection_hash:
            return self.history.get(connection_hash, None)
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
    history: History | None,
) -> None:
    if connection_hash is None and s3_tree is None:
        return
    cache = _load_cache()
    if cache is None:
        cache = CatalogCache(databases={}, s3={}, history={})
    # if catalog is not None and connection_hash:
    #     cache.databases[connection_hash] = catalog
    if s3_tree is not None and s3_tree.catalog_data is not None:
        cache.s3[s3_tree.cache_key] = s3_tree.catalog_data
    if history is not None and connection_hash:
        cache.history[connection_hash] = history
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
        EOFError,
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
