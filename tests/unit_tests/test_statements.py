"""The tricky-SQL corpus.

`tests/functional_tests/test_query_editor.py` exercises the same splitter
through the Query Editor, so a divergence between the two front ends fails
here or there rather than in a user's buffer.
"""

from __future__ import annotations

import pytest

from harlequin.statements import Statement, find_separators, split

# (name, script, expected statements). The name is the pytest id.
CORPUS: list[tuple[str, str, list[str]]] = [
    ("empty", "", []),
    ("whitespace only", "   \n\t\n  ", []),
    ("no separator", "select 1", ["select 1"]),
    ("trailing separator", "select 1;", ["select 1;"]),
    ("two statements", "select 1; select 2", ["select 1;", "select 2"]),
    (
        "two statements, both terminated",
        "select 1;\nselect 2;\n",
        ["select 1;", "select 2;"],
    ),
    ("bare separator", ";", [";"]),
    (
        "consecutive separators",
        "select 1;;select 2",
        ["select 1;", ";", "select 2"],
    ),
    (
        "blank lines between statements",
        "select 1;\n\n\n   \n\nselect 2;",
        ["select 1;", "select 2;"],
    ),
    # a semicolon inside any of these is part of a single node, so the grammar
    # never offers it as a separator.
    ("single-quoted literal", "select 'a;b'", ["select 'a;b'"]),
    (
        "single-quoted literal, then a real separator",
        "select 'a;b'; select 2",
        ["select 'a;b';", "select 2"],
    ),
    ("double-quoted identifier", 'select "a;b" from t', ['select "a;b" from t']),
    ("line comment", "select 1 -- a; comment\n", ["select 1 -- a; comment"]),
    (
        "line comment after a separator",
        "select 1; -- a; comment\nselect 2",
        ["select 1;", "-- a; comment\nselect 2"],
    ),
    (
        "block comment",
        "select 1 /* a; comment */ from t",
        ["select 1 /* a; comment */ from t"],
    ),
    (
        "escaped quote inside a literal",
        "select 'it''s; fine'; select 2",
        ["select 'it''s; fine';", "select 2"],
    ),
    # the reason find_separators() returns character columns. A byte offset
    # lands one position late per byte of overhead: one for é, six for 日本語.
    # The first is the reproduction from #1015.
    (
        "non-ascii before a separator",
        "select 'café';select 2",
        ["select 'café';", "select 2"],
    ),
    (
        "multibyte before a separator",
        "select '日本語';select 2",
        ["select '日本語';", "select 2"],
    ),
    (
        "non-ascii on an earlier line",
        "select 'né';\nselect 2;",
        ["select 'né';", "select 2;"],
    ),
    (
        "astral plane before a separator",
        "select '🦜';select 2",
        ["select '🦜';", "select 2"],
    ),
    (
        "non-ascii identifier",
        'select "日本" from t; select 2',
        ['select "日本" from t;', "select 2"],
    ),
    (
        "windows line endings",
        "select 1;\r\nselect 2;\r\n",
        ["select 1;", "select 2;"],
    ),
    (
        "many statements on one line",
        "select 1;select 2;select 3",
        ["select 1;", "select 2;", "select 3"],
    ),
    # a semicolon inside a dollar-quoted body is body content, not a
    # separator. The grammar exposes the body's `$tag$` delimiters either way:
    # as function_body tags when it parses, and as bare tokens during error
    # recovery when it cannot (a missing `returns` clause). See #1019.
    (
        "dollar-quoted body with returns",
        "create function f() returns int as $$ select 1; $$; select 2",
        ["create function f() returns int as $$ select 1; $$;", "select 2"],
    ),
    (
        "dollar-quoted body, semicolon flush with delimiters",
        "create function f() as $$;$$; select 2",
        ["create function f() as $$;$$;", "select 2"],
    ),
    (
        "labeled dollar-quoted body",
        "create function f() returns int as $body$ select 1; $body$; select 2",
        ["create function f() returns int as $body$ select 1; $body$;", "select 2"],
    ),
    (
        "differently labeled delimiter inside a body",
        "create function f() as $a$ select 1; $b$ 2; $a$; select 3",
        ["create function f() as $a$ select 1; $b$ 2; $a$;", "select 3"],
    ),
    (
        "two dollar-quoted bodies",
        "create function f() as $$ a; $$; create function g() as $$ b; $$;",
        ["create function f() as $$ a; $$;", "create function g() as $$ b; $$;"],
    ),
    (
        "unmatched dollar-quote delimiter",
        "select $tag$; select 2",
        ["select $tag$;", "select 2"],
    ),
    (
        "dollar-quote tag in a string literal",
        "select '$$'; select 2",
        ["select '$$';", "select 2"],
    ),
    (
        "dollar-quote tag in a comment",
        "select 1 -- $$ ; select 2",
        ["select 1 -- $$ ; select 2"],
    ),
    (
        "dollar-quoted literal in an expression",
        "select $$a;b$$; select 2",
        ["select $$a;b$$;", "select 2"],
    ),
    (
        "transaction",
        "begin; select 1; commit;",
        ["begin;", "select 1;", "commit;"],
    ),
    (
        "multiline dollar-quoted body",
        "create function f() as $$\n select 1;\n$$;\nselect 2",
        ["create function f() as $$\n select 1;\n$$;", "select 2"],
    ),
    (
        "non-ascii inside a dollar-quoted body",
        "create function f() as $$ select 'café'; $$; select 2",
        ["create function f() as $$ select 'café'; $$;", "select 2"],
    ),
    (
        "multiline dollar-quoted body with non-ascii",
        "create function f() as $$\n select 'café';\n$$;\nselect 2",
        ["create function f() as $$\n select 'café';\n$$;", "select 2"],
    ),
]


@pytest.mark.parametrize(
    ("script", "expected"),
    [(script, expected) for _, script, expected in CORPUS],
    ids=[name for name, _, _ in CORPUS],
)
def test_split(script: str, expected: list[str]) -> None:
    assert split(script) == [
        Statement(sql=sql, index=i) for i, sql in enumerate(expected)
    ]


@pytest.mark.parametrize(
    ("script", "expected"),
    [(script, expected) for _, script, expected in CORPUS],
    ids=[name for name, _, _ in CORPUS],
)
def test_find_separators_agrees_with_split(script: str, expected: list[str]) -> None:
    """Slicing a buffer at the separators must reproduce split()'s statements.

    This is the property the Query Editor relies on: it slices with the points,
    while `-f` slices with the offsets, and the two must not disagree.
    """
    lines = script.splitlines(keepends=True)
    line_starts = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line))

    offsets = [line_starts[row] + col for row, col in find_separators(script)]
    sliced = []
    start = 0
    for end in [*offsets, len(script)]:
        if sql := script[start:end].strip():
            sliced.append(sql)
        start = end

    assert sliced == expected


def test_find_separators_returns_character_columns() -> None:
    """Regression test for #1015, the byte-vs-character bug this module fixes.

    tree-sitter reports `Point.column` in bytes; `café` is 5 bytes and 4
    characters, so the raw node put the separator at column 15 instead of 14,
    and `日本語` (9 bytes, 3 characters) at 19 instead of 13.
    """
    assert find_separators("select 'café';select 2") == [(0, 14)]
    assert find_separators("select '日本語';select 2") == [(0, 13)]


def test_find_separators_on_multiple_lines() -> None:
    script = "select 1;\nselect 'né';\nselect 3;"
    assert find_separators(script) == [(0, 9), (1, 12), (2, 9)]


def test_find_separators_are_sorted() -> None:
    """tree-sitter captures nodes in pattern-match order, not buffer order."""
    script = "".join(f"select {i};\n" for i in range(50))
    points = find_separators(script)
    assert points == sorted(points)
    assert len(points) == 50


def test_statements_are_indexed_in_order() -> None:
    statements = split("select 1; select 2; select 3")
    assert [s.index for s in statements] == [0, 1, 2]


def test_dollar_quoted_body_is_not_split() -> None:
    """Regression test for #1019: a semicolon inside `$$ ... $$` is body content."""
    script = "create function f() as $$ select 1; $$; select 2"
    assert split(script) == [
        Statement(sql="create function f() as $$ select 1; $$;", index=0),
        Statement(sql="select 2", index=1),
    ]


def test_find_separators_after_dollar_quoted_body() -> None:
    """The separator after a dollar-quoted body is still a character column.

    The fix pairs the body's delimiters on byte offsets, and a multiline body
    puts non-ASCII before the separator that ends the statement -- so the
    (row, col) point must come out of the same character conversion as any
    other separator, not off by one per extra byte.
    """
    script = "create function f() as $$\n select 'café';\n$$;\nselect 2"
    assert find_separators(script) == [(2, 3)]
