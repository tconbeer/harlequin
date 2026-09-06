from __future__ import annotations

import pytest

from harlequin.autocomplete import BufferSymbols, find_symbols

# (name, sql, expected names, expected members). The name is the pytest id.
CORPUS: list[tuple[str, str, list[str], list[tuple[str, str]]]] = [
    ("empty", "", [], []),
    ("whitespace only", "  \n\t ", [], []),
    ("keywords only", "select 1", [], []),
    ("one table", "select * from my_table", ["my_table"], []),
    (
        "columns and aliases",
        "select foo as bar, baz from t",
        ["foo", "bar", "baz", "t"],
        [],
    ),
    (
        "qualified table",
        "select * from my_db.my_schema.my_table",
        ["my_db", "my_schema", "my_table"],
        [("my_db", "my_schema"), ("my_schema", "my_table")],
    ),
    (
        "aliased columns",
        "select t.a, t.b from my_table t",
        ["t", "a", "b", "my_table"],
        [("t", "a"), ("t", "b")],
    ),
    (
        "cte",
        "with my_cte as (select 1 as n) select my_cte.n from my_cte",
        ["my_cte", "n"],
        [("my_cte", "n")],
    ),
    (
        "quoted identifiers",
        'select t."Mixed Case" from "My Table" t',
        ["t", "Mixed Case", "My Table"],
        [("t", "Mixed Case")],
    ),
    # the user is mid-edit far more often than not, so the parse has to keep
    # going past whatever they have not finished typing.
    ("unterminated member", "select * from my_table t where t.", ["my_table", "t"], []),
    (
        "half-typed clause",
        "select a, from my_table",
        ["a", "from", "my_table"],
        [],
    ),
    (
        "several statements",
        "select a from one; select b from two",
        ["a", "one", "b", "two"],
        [],
    ),
    # a symbol is worth offering once, under the spelling it first appeared in.
    (
        "repeated, in mixed case",
        "select FOO from t union all select foo from t",
        ["FOO", "t"],
        [],
    ),
    # semicolons and words inside these belong to one node, so nothing in them
    # is an identifier.
    ("string literal", "select 'not_a_symbol' from t", ["t"], []),
    ("comment", "select a from t -- not_a_symbol\n", ["a", "t"], []),
]


@pytest.mark.parametrize(
    "sql,expected_names,expected_members",
    [pytest.param(*case[1:], id=case[0]) for case in CORPUS],
)
def test_find_symbols(
    sql: str, expected_names: list[str], expected_members: list[tuple[str, str]]
) -> None:
    assert find_symbols(sql) == BufferSymbols(
        names=tuple(expected_names), members=tuple(expected_members)
    )
