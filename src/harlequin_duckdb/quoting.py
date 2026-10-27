from __future__ import annotations

import re

_BARE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
"""What DuckDB reads as an identifier without quotes, keywords aside."""


def quote_identifier(name: str) -> str:
    """name as a double-quoted DuckDB identifier, with any `"` in it doubled."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(text: str) -> str:
    """text as a DuckDB string literal, with any `'` in it doubled.

    A backslash is an ordinary character in a standard string, so it is left
    as it is.
    """
    return "'" + text.replace("'", "''") + "'"


def qualified_name(*names: str) -> str:
    """Each name quoted, joined with dots."""
    return ".".join(quote_identifier(name) for name in names)


def quote_identifier_if_needed(name: str, reserved_keywords: set[str]) -> str:
    """name bare if DuckDB would read it as that identifier unquoted, else quoted."""
    if _BARE_IDENTIFIER.fullmatch(name) and name.lower() not in reserved_keywords:
        return name
    return quote_identifier(name)
