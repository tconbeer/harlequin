"""Write the version-2 catalog cache that `legacy_history_cache` copies.

A version-2 pickle names `harlequin.catalog_cache.CatalogCache` and
`harlequin.history.History`/`QueryExecution` by module path, so the fixture is
only worth what it was written by: run this against a *released* Harlequin that
still has that version, never against the working tree, which no longer has it.

    uv run --with 'harlequin==2.14.0' --no-project python scripts/write_legacy_cache.py

The committed file is what pins the on-disk shape; regenerate it only to add a
record, and only the same way.
"""

from __future__ import annotations

import pickle
from collections import deque
from datetime import datetime
from pathlib import Path

import harlequin.catalog_cache as catalog_cache
from harlequin.history import History, QueryExecution

OUT = Path(__file__).parent.parent / "tests" / "data" / "catalog-cache-2.pickle"

HISTORY: dict[str, list[tuple[str, datetime, int, float]]] = {
    "abc123": [
        ("select 1", datetime(2026, 8, 1, 9, 30), 1, 0.5),
        ("sel", datetime(2026, 8, 1, 9, 31), -1, 0.0),
    ],
    "foo": [("select * from line_items", datetime(2026, 8, 1, 10, 30), 3, 1.25)],
}
"""Kept in step with `tests/conftest.py::LEGACY_HISTORY`, which is what the
tests assert against."""


def main() -> None:
    if catalog_cache.CACHE_VERSION != 2:
        raise SystemExit(
            f"this Harlequin writes version {catalog_cache.CACHE_VERSION} caches; "
            "run it against a release that still writes version 2"
        )
    cache = catalog_cache.CatalogCache(
        databases={},
        s3={},
        history={
            connection: History(
                queries=deque(
                    [
                        QueryExecution(
                            query_text=sql,
                            executed_at=ran_at,
                            result_row_count=rows,
                            elapsed=elapsed,
                        )
                        for sql, ran_at, rows, elapsed in records
                    ],
                    maxlen=500,
                )
            )
            for connection, records in HISTORY.items()
        },
    )
    OUT.write_bytes(pickle.dumps(cache))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
