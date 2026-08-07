from __future__ import annotations

import pytest

from harlequin.order_by import parse_trailing_order_by, with_order_by


@pytest.mark.parametrize(
    "sql,expected",
    [
        # appended when the query has no tail at all
        ("select * from t", 'select * from t\norder by "a" asc'),
        # inserted before an existing limit, so the database sorts then limits
        (
            "select * from t\nlimit 100",
            'select * from t\norder by "a" asc\nlimit 100',
        ),
        ("select * from t limit 100", 'select * from t\norder by "a" asc\nlimit 100'),
        # limit + offset both stay in the tail
        (
            "select * from t limit 10 offset 20",
            'select * from t\norder by "a" asc\nlimit 10 offset 20',
        ),
        # an existing simple order by is replaced, not stacked
        (
            'select * from t order by "b" desc limit 5',
            'select * from t\norder by "a" asc\nlimit 5',
        ),
        ("select * from t order by b", 'select * from t\norder by "a" asc'),
        # a trailing semicolon survives
        ("select * from t;", 'select * from t\norder by "a" asc;'),
        (
            "select * from t limit 3 ;",
            'select * from t\norder by "a" asc\nlimit 3;',
        ),
    ],
)
def test_with_order_by_rewrites_the_tail(sql: str, expected: str) -> None:
    assert with_order_by(sql, "a", descending=False) == expected


def test_with_order_by_descending() -> None:
    assert with_order_by("select * from t", "a", descending=True) == (
        'select * from t\norder by "a" desc'
    )


def test_column_names_are_quoted() -> None:
    """Spaces, capitals and keywords must survive, and quotes must be escaped."""
    assert 'order by "user name" asc' in with_order_by("select 1", "user name", False)
    assert 'order by "select" asc' in with_order_by("select 1", "select", False)
    assert 'order by "a""b" asc' in with_order_by("select 1", 'a"b', False)


@pytest.mark.parametrize(
    "sql",
    [
        # a window function's order by is inside parens, so it is not the query's
        "select row_number() over (order by b) from t",
        # so is a subquery's
        "select * from (select * from t order by b limit 2) x",
        # and a CTE's
        "with c as (select * from t order by b) select * from c",
    ],
)
def test_nested_order_by_is_not_mistaken_for_the_query_s_own(sql: str) -> None:
    result = with_order_by(sql, "a", descending=False)
    assert result == f'{sql}\norder by "a" asc'
    # the nested clause is still intact
    assert "order by b" in result


def test_keywords_inside_strings_and_comments_are_ignored() -> None:
    sql = "select 'order by x' as s, 1 -- limit 5\nfrom t"
    assert with_order_by(sql, "a", False) == f'{sql}\norder by "a" asc'

    block = "select 1 /* order by b limit 2 */ from t"
    assert with_order_by(block, "a", False) == f'{block}\norder by "a" asc'


def test_quoted_identifier_containing_a_keyword_is_ignored() -> None:
    sql = 'select "limit" from t'
    assert with_order_by(sql, "a", False) == f'{sql}\norder by "a" asc'


def test_replacing_an_order_by_keeps_a_nested_one() -> None:
    sql = "select (select max(x) from u order by x limit 1) from t order by b limit 9"
    result = with_order_by(sql, "a", descending=True)
    assert result == (
        "select (select max(x) from u order by x limit 1) from t\n"
        'order by "a" desc\n'
        "limit 9"
    )


def test_round_trip_is_stable() -> None:
    """Clicking the same header repeatedly must not accumulate clauses."""
    sql = "select * from t limit 100"
    once = with_order_by(sql, "a", descending=False)
    twice = with_order_by(once, "a", descending=True)
    thrice = with_order_by(twice, "a", descending=False)
    assert twice == 'select * from t\norder by "a" desc\nlimit 100'
    assert thrice == once
    assert once.count("order by") == 1


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("select * from t", None),
        ("select * from t limit 5", None),
        ('select * from t order by "a"', ("a", False)),
        ('select * from t order by "a" asc', ("a", False)),
        ('select * from t order by "a" desc', ("a", True)),
        ("select * from t order by a DESC", ("a", True)),
        ('select * from t order by "a" desc limit 5', ("a", True)),
        ('select * from t order by "user name" asc', ("user name", False)),
        # not round-trippable through a single header, so report no sort
        ("select * from t order by a, b", None),
        ("select * from t order by lower(a)", None),
        ("select * from t order by a nulls first", None),
        # nested clauses are not the query's own sort
        ("select row_number() over (order by b) from t", None),
    ],
)
def test_parse_trailing_order_by(sql: str, expected: tuple[str, bool] | None) -> None:
    assert parse_trailing_order_by(sql) == expected


def test_parse_round_trips_with_writer() -> None:
    written = with_order_by("select * from t limit 100", "some col", descending=True)
    assert parse_trailing_order_by(written) == ("some col", True)
