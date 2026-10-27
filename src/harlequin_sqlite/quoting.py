from __future__ import annotations


def quote_identifier(name: str) -> str:
    """name as a double-quoted SQLite identifier, with any `"` in it doubled."""
    return '"' + name.replace('"', '""') + '"'
