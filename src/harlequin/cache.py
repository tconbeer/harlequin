"""
This is required for backward-compatibility for previously-pickled caches.

cache.py was renamed to editor_cache.py when the Data Catalog cache was created.
"""

from harlequin.editor_cache import BufferState, Cache

__all__ = ["BufferState", "Cache"]
