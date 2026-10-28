from __future__ import annotations

import sqlite3
from types import ModuleType

import duckdb
import pytest

from harlequin_duckdb import quoting as duckdb_quoting
from harlequin_sqlite import quoting as sqlite_quoting

AWKWARD_NAMES = [
    "Bob's orders",
    'say "hi"',
    "a.b",
    "semi;colon",
    "back\\slash",
    "back\\",
    "tick`s",
    "brack]et",
    "Mixed Case",
    "select",
    "2024-sales",
    "--comment",
    "",
]


@pytest.mark.parametrize("quoting", [duckdb_quoting, sqlite_quoting])
def test_quote_identifier_doubles_a_double_quote(quoting: ModuleType) -> None:
    assert quoting.quote_identifier('say "hi"') == '"say ""hi"""'
    assert quoting.quote_identifier("it's") == '"it\'s"'


@pytest.mark.parametrize("quoting", [duckdb_quoting, sqlite_quoting])
def test_quote_literal_doubles_a_single_quote_and_keeps_a_backslash(
    quoting: ModuleType,
) -> None:
    assert quoting.quote_literal('it\'s \\ "x"') == "'it''s \\ \"x\"'"


@pytest.mark.parametrize("quoting", [duckdb_quoting, sqlite_quoting])
def test_qualified_name_quotes_each_part(quoting: ModuleType) -> None:
    assert quoting.qualified_name("a.b", 'c"d') == '"a.b"."c""d"'


@pytest.mark.parametrize("name", [name for name in AWKWARD_NAMES if name])
def test_duckdb_reads_back_what_was_quoted(name: str) -> None:
    conn = duckdb.connect()
    assert conn.execute(f"select {duckdb_quoting.quote_literal(name)}").fetchall() == [
        (name,)
    ]
    table = duckdb_quoting.qualified_name("main", name)
    column = duckdb_quoting.quote_identifier(name)
    conn.execute(f"create table {table} ({column} int)")
    assert conn.execute(
        "select table_name, column_name from information_schema.columns"
    ).fetchall() == [(name, name)]


@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_sqlite_reads_back_what_was_quoted(name: str) -> None:
    conn = sqlite3.connect(":memory:")
    assert conn.execute(f"select {sqlite_quoting.quote_literal(name)}").fetchall() == [
        (name,)
    ]
    if not name:
        return
    table = sqlite_quoting.qualified_name("main", name)
    column = sqlite_quoting.quote_identifier(name)
    conn.execute(f"create table {table} ({column} int)")
    assert conn.execute(
        "select m.name, p.name from sqlite_schema m join pragma_table_info(m.name) p"
    ).fetchall() == [(name, name)]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("my_macro", "my_macro"),
        ("MyMacro", "MyMacro"),
        ("my macro", '"my macro"'),
        ("2024_total", '"2024_total"'),
        ("select", '"select"'),
        ("Select", '"Select"'),
        ("between", '"between"'),
        ('a"b', '"a""b"'),
    ],
)
def test_duckdb_quotes_an_identifier_only_when_bare_would_misread_it(
    name: str, expected: str
) -> None:
    assert (
        duckdb_quoting.quote_identifier_if_needed(name, {"select", "between"})
        == expected
    )
