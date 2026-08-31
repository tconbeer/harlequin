from __future__ import annotations

from decimal import Decimal

import pytest

from harlequin.app import Harlequin


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "null"),
        (True, "true"),
        (False, "false"),
        (42, "42"),
        (0, "0"),
        (-7, "-7"),
        (3.14, "3.14"),
        (Decimal("10"), "'10'"),  # not int/float -> quoted
        ("abc", "'abc'"),
        ("it's", "'it''s'"),  # single quotes are doubled/escaped
        ("a-uuid-like-string", "'a-uuid-like-string'"),
    ],
)
def test_sql_literal_renders_where_clause_values(value: object, expected: str) -> None:
    """The foreign-key navigation query builds a WHERE clause from a cell value;
    numbers are bare, everything else is single-quoted with quotes escaped."""
    assert Harlequin._sql_literal(value) == expected
