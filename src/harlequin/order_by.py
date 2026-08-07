"""Read and rewrite the ORDER BY clause of a query, for sort-by-header-click.

Harlequin has no SQL parser, and pulling one in for a UI affordance would be a
heavy dependency. What this needs is much narrower than parsing: find the
query's *own* trailing ORDER BY and LIMIT, which means recognising keywords that
sit at parenthesis depth zero and outside string literals, quoted identifiers
and comments. A window function's `over (order by ...)`, a CTE's inner ORDER BY
and the word "limit" inside a string are all nested or quoted, so depth-zero
scanning steps over them.

Known gap: PostgreSQL dollar-quoted strings ($$ ... $$) are not recognised, so a
top-level keyword inside one would be misread. That does not occur in the
SELECTs the Results Viewer displays.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")

_TAIL_KEYWORDS = frozenset({"limit", "offset", "fetch"})
"""Clauses that may legally follow ORDER BY, and so mark where it ends."""

_SIMPLE_ORDER_BY = re.compile(
    r"""
    order \s+ by \s+
    (?: " (?P<quoted> (?: [^"] | "" )+ ) "     # "quoted identifier"
      | (?P<bare> [A-Za-z_][A-Za-z0-9_$]* )    # or a bare one
    )
    (?: \s+ (?P<direction> asc | desc ) )? \s* $
    """,
    re.IGNORECASE | re.VERBOSE,
)


def quote_identifier(name: str) -> str:
    """Double-quote an identifier so it survives spaces, case and keywords."""
    return '"' + name.replace('"', '""') + '"'


def _top_level_words(sql: str) -> list[tuple[int, str]]:
    """(offset, lowercased word) for every word at paren depth 0.

    Content inside quotes and comments is skipped entirely, so keywords there
    are never mistaken for clauses.
    """
    words: list[tuple[int, str]] = []
    i = 0
    n = len(sql)
    depth = 0
    while i < n:
        ch = sql[i]
        if ch in "'\"`":
            quote = ch
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:  # escaped by doubling
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline + 1
            continue
        if sql.startswith("/*", i):
            close = sql.find("*/", i + 2)
            i = n if close == -1 else close + 2
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        match = _WORD.match(sql, i)
        if match:
            if depth == 0:
                words.append((match.start(), match.group(0).lower()))
            i = match.end()
            continue
        i += 1
    return words


def _split_tail(sql: str) -> tuple[int | None, int | None]:
    """Offsets of the query's own ORDER BY and of the clause that follows it.

    Returns (order_by_start, tail_start); either may be None. tail_start is the
    LIMIT/OFFSET/FETCH that terminates the ORDER BY, or that ends the query when
    there is no ORDER BY.
    """
    words = _top_level_words(sql)
    order_start: int | None = None
    for index in range(len(words) - 1):
        if words[index][1] == "order" and words[index + 1][1] == "by":
            order_start = words[index][0]  # the last one wins; it is the outer query's
    tail_start: int | None = None
    for offset, word in words:
        if word in _TAIL_KEYWORDS and (order_start is None or offset > order_start):
            tail_start = offset
            break
    return order_start, tail_start


def _strip_terminator(sql: str) -> tuple[str, str]:
    """Split trailing semicolons off, so they can be put back after rewriting."""
    body = sql.rstrip()
    terminator = ""
    while body.endswith(";"):
        body = body[:-1].rstrip()
        terminator = ";"
    return body, terminator


def parse_trailing_order_by(sql: str) -> tuple[str, bool] | None:
    """The (column, descending) this query already sorts by, if it is a simple one.

    Returns None when the query has no ORDER BY, or when it sorts by something
    the header UI cannot round-trip -- several columns, an expression, NULLS
    FIRST and so on. Those are left alone rather than silently rewritten.
    """
    body, _ = _strip_terminator(sql)
    order_start, tail_start = _split_tail(body)
    if order_start is None:
        return None
    clause = body[order_start : tail_start if tail_start is not None else len(body)]
    match = _SIMPLE_ORDER_BY.match(clause.strip())
    if match is None:
        return None
    quoted = match.group("quoted")
    column = quoted.replace('""', '"') if quoted is not None else match.group("bare")
    return column, (match.group("direction") or "").lower() == "desc"


def with_order_by(sql: str, column: str, descending: bool) -> str:
    """Return `sql` sorted by `column`, replacing any ORDER BY it already has.

    The clause is inserted before LIMIT/OFFSET rather than wrapping the query in
    a subquery, so the database sorts the whole result and the limit then takes
    the true top rows -- wrapping would only reorder the rows already fetched.
    """
    body_sql, terminator = _strip_terminator(sql)
    order_start, tail_start = _split_tail(body_sql)

    cut = order_start if order_start is not None else tail_start
    body = body_sql if cut is None else body_sql[:cut]
    tail = "" if tail_start is None else body_sql[tail_start:]

    clause = f"order by {quote_identifier(column)} {'desc' if descending else 'asc'}"
    parts = [body.rstrip(), clause]
    if tail.strip():
        parts.append(tail.strip())
    return "\n".join(parts) + terminator
