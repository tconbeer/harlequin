from pathlib import Path

import pytest
from harlequin.duck_ops import (
    connect,
    get_catalog,
    get_columns,
    get_databases,
    get_schemas,
    get_tables,
)
from harlequin.exception import HarlequinExit


def test_connect(tiny_db: Path, small_db: Path, tmp_path: Path) -> None:
    assert connect([])
    assert connect([":memory:"])
    assert connect([tiny_db], read_only=False)
    assert connect([tiny_db], read_only=True)
    assert connect([tiny_db, Path(":memory:"), small_db], read_only=False)
    assert connect([tiny_db, small_db], read_only=True)
    assert connect([tmp_path / "new.db"])


def test_connect_motherduck(tiny_db: Path) -> None:
    # note: set environment variable motherduck_token
    assert connect(["md:"])
    assert connect(["md:cloudf1"], md_saas=True)
    assert connect(["md:", tiny_db])


def test_cannot_connect(tiny_db: Path) -> None:
    with pytest.raises(HarlequinExit):
        connect([Path(":memory:")], read_only=True)
    with pytest.raises(HarlequinExit):
        connect([tiny_db, Path(":memory:")], read_only=True)


def test_get_databases(tiny_db: Path, small_db: Path) -> None:
    conn = connect([tiny_db, small_db])
    assert get_databases(conn) == [("small",), ("tiny",)]


def test_get_schemas(small_db: Path) -> None:
    conn = connect([small_db], read_only=True)
    assert get_schemas(conn, "small") == [("empty",), ("main",)]


def test_get_tables(small_db: Path) -> None:
    conn = connect([small_db], read_only=True)
    assert get_tables(conn, "small", "empty") == []
    assert get_tables(conn, "small", "main") == [("drivers", "BASE TABLE")]


def test_get_columns(small_db: Path) -> None:
    conn = connect([small_db], read_only=True)
    assert get_columns(conn, "small", "main", "drivers") == [
        ("code", "VARCHAR"),
        ("dob", "DATE"),
        ("driverId", "BIGINT"),
        ("driverRef", "VARCHAR"),
        ("forename", "VARCHAR"),
        ("nationality", "VARCHAR"),
        ("number", "VARCHAR"),
        ("surname", "VARCHAR"),
        ("url", "VARCHAR"),
    ]


def test_get_catalog(tiny_db: Path, small_db: Path) -> None:
    conn = connect([tiny_db, small_db], read_only=True)
    expected = [
        (
            "small",
            [
                ("empty", []),
                (
                    "main",
                    [
                        (
                            "drivers",
                            "BASE TABLE",
                            [
                                ("code", "VARCHAR"),
                                ("dob", "DATE"),
                                ("driverId", "BIGINT"),
                                ("driverRef", "VARCHAR"),
                                ("forename", "VARCHAR"),
                                ("nationality", "VARCHAR"),
                                ("number", "VARCHAR"),
                                ("surname", "VARCHAR"),
                                ("url", "VARCHAR"),
                            ],
                        )
                    ],
                ),
            ],
        ),
        ("tiny", [("main", [("foo", "BASE TABLE", [("foo_col", "INTEGER")])])]),
    ]
    assert get_catalog(conn) == expected
