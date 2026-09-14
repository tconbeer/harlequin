from __future__ import annotations

import pickle
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING

from platformdirs import user_cache_dir

from harlequin.catalog import Catalog
from harlequin.query_log import (
    QueryLog,
    get_connection_hash,  # re-exported
    recent,
)

if TYPE_CHECKING:
    from harlequin.components.data_catalog import S3Tree

CACHE_VERSION = 3

HISTORY_CACHE_VERSION = 2
"""The last version that held the query history, which the store now holds."""

__all__ = [
    "CatalogCache",
    "get_catalog_cache",
    "get_connection_hash",
    "migrate_pickled_history",
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


def migrate_pickled_history(connection_hash: str | None) -> int:
    """Copy a pre-3 cache's queries for one connection into the query log.

    Once per connection, and never over rows that are already there: a store
    holding any of this connection's queries has either been migrated or been
    written to since, and the pickle is the older record either way. Returns
    how many records moved.
    """
    if not connection_hash:
        return 0
    cache_file = _get_cache_file(HISTORY_CACHE_VERSION)
    if not cache_file.exists():
        return 0
    try:
        if recent(connection=connection_hash, limit=1):
            return 0
    except sqlite3.Error:
        return 0
    cache = _load_cache(cache_file)
    if cache is None:
        return 0
    # the field this class no longer declares: what a version-2 pickle carries
    history = getattr(cache, "history", {}).get(connection_hash)
    if history is None:
        return 0
    log = QueryLog(program="harlequin", connection=connection_hash)
    migrated = 0
    try:
        for record in history:
            # a version-2 record had no status: a negative row count is how it
            # said the query failed
            failed = (record.result_row_count or 0) < 0
            written = log.write(
                record.query_text,
                status="error" if failed else "ok",
                rows=None if failed else record.result_row_count,
                elapsed_ms=record.elapsed * 1000,
                # recorded in local time, and the store keeps UTC
                run_at=record.executed_at.astimezone(timezone.utc),
            )
            if written is not None:
                migrated += 1
    finally:
        log.close()
    return migrated


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
