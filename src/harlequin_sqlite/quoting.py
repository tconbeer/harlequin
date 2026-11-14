from __future__ import annotations


def quote_identifier(name: str) -> str:
    """name as a double-quoted SQLite identifier, with any `"` in it doubled."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(text: str) -> str:
    """text as a SQLite string literal, with any `'` in it doubled.

    SQLite has no backslash escapes, so a backslash is left as it is.
    """
    return "'" + text.replace("'", "''") + "'"


def qualified_name(*names: str) -> str:
    """Each name quoted, joined with dots."""
    return ".".join(quote_identifier(name) for name in names)
