import sys
from pathlib import Path

import pytest
from harlequin.catalog import Catalog, CatalogItem
from harlequin.duck_ops import (
    _get_columns,
    _get_databases,
    _get_schemas,
    _get_tables,
    _rewrite_init_command,
    _split_script,
    connect,
    get_catalog,
)
from harlequin.exception import HarlequinConnectionError


def test_connect(tiny_db: Path, small_db: Path, tmp_path: Path) -> None:
    assert connect([])
    assert connect([":memory:"])
    assert connect([tiny_db], read_only=False)
    assert connect([tiny_db], read_only=True)
    assert connect([tiny_db, Path(":memory:"), small_db], read_only=False)
    assert connect([tiny_db, small_db], read_only=True)
    assert connect([tmp_path / "new.db"])
    assert connect([], allow_unsigned_extensions=True)
    assert connect([tiny_db], allow_unsigned_extensions=True)
    assert connect([tiny_db, small_db], read_only=True)


@pytest.mark.online
def test_connect_extensions() -> None:
    assert connect([], extensions=None)
    assert connect([], extensions=[])
    assert connect([], extensions=["spatial"])
    assert connect([], allow_unsigned_extensions=True, extensions=["spatial"])


@pytest.mark.xfail(
    sys.platform == "win32",
    reason="PRQL extension not yet built for Windows and DuckDB v0.8.1.",
)
@pytest.mark.online
def test_connect_prql() -> None:
    # Note: this may fail in the future if the extension doesn't support the latest
    # duckdb version.
    assert connect(
        [],
        allow_unsigned_extensions=True,
        extensions=["prql"],
        custom_extension_repo="welsch.lu/duckdb/prql/latest",
        force_install_extensions=True,
    )


@pytest.mark.skipif(
    sys.version_info[0:2] != (3, 10), reason="Matrix is hitting MD too many times."
)
@pytest.mark.online
def test_connect_motherduck(tiny_db: Path) -> None:
    # note: set environment variable motherduck_token
    assert connect(["md:"])
    assert connect(["md:cloudf1"], md_saas=True)
    assert connect(["md:", tiny_db])


def test_cannot_connect(tiny_db: Path) -> None:
    with pytest.raises(HarlequinConnectionError):
        connect([Path(":memory:")], read_only=True)
    with pytest.raises(HarlequinConnectionError):
        connect([tiny_db, Path(":memory:")], read_only=True)


def test_get_databases(tiny_db: Path, small_db: Path) -> None:
    conn = connect([tiny_db, small_db])
    assert _get_databases(conn) == [("small",), ("tiny",)]


def test_get_schemas(small_db: Path) -> None:
    conn = connect([small_db], read_only=True)
    assert _get_schemas(conn, "small") == [("empty",), ("main",)]


def test_get_tables(small_db: Path) -> None:
    conn = connect([small_db], read_only=True)
    assert _get_tables(conn, "small", "empty") == []
    assert _get_tables(conn, "small", "main") == [("drivers", "BASE TABLE")]


def test_get_columns(small_db: Path) -> None:
    conn = connect([small_db], read_only=True)
    assert _get_columns(conn, "small", "main", "drivers") == [
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
    expected = Catalog(
        items=[
            CatalogItem(
                qualified_identifier='"small"',
                query_name='"small"',
                label="small",
                type_label="db",
                children=[
                    CatalogItem(
                        qualified_identifier='"small"."empty"',
                        query_name='"small"."empty"',
                        label="empty",
                        type_label="sch",
                        children=[],
                    ),
                    CatalogItem(
                        qualified_identifier='"small"."main"',
                        query_name='"small"."main"',
                        label="main",
                        type_label="sch",
                        children=[
                            CatalogItem(
                                qualified_identifier='"small"."main"."drivers"',
                                query_name='"small"."main"."drivers"',
                                label="drivers",
                                type_label="t",
                                children=[
                                    CatalogItem(
                                        qualified_identifier='"small"."main"."drivers"."code"',
                                        query_name='"code"',
                                        label="code",
                                        type_label="s",
                                    ),
                                    CatalogItem(
                                        qualified_identifier='"small"."main"."drivers"."dob"',
                                        query_name='"dob"',
                                        label="dob",
                                        type_label="d",
                                    ),
                                    CatalogItem(
                                        qualified_identifier='"small"."main"."drivers"."driverId"',
                                        query_name='"driverId"',
                                        label="driverId",
                                        type_label="##",
                                    ),
                                    CatalogItem(
                                        qualified_identifier='"small"."main"."drivers"."driverRef"',
                                        query_name='"driverRef"',
                                        label="driverRef",
                                        type_label="s",
                                    ),
                                    CatalogItem(
                                        qualified_identifier='"small"."main"."drivers"."forename"',
                                        query_name='"forename"',
                                        label="forename",
                                        type_label="s",
                                    ),
                                    CatalogItem(
                                        qualified_identifier='"small"."main"."drivers"."nationality"',
                                        query_name='"nationality"',
                                        label="nationality",
                                        type_label="s",
                                    ),
                                    CatalogItem(
                                        qualified_identifier='"small"."main"."drivers"."number"',
                                        query_name='"number"',
                                        label="number",
                                        type_label="s",
                                    ),
                                    CatalogItem(
                                        qualified_identifier='"small"."main"."drivers"."surname"',
                                        query_name='"surname"',
                                        label="surname",
                                        type_label="s",
                                    ),
                                    CatalogItem(
                                        qualified_identifier='"small"."main"."drivers"."url"',
                                        query_name='"url"',
                                        label="url",
                                        type_label="s",
                                    ),
                                ],
                            )
                        ],
                    ),
                ],
            ),
            CatalogItem(
                qualified_identifier='"tiny"',
                query_name='"tiny"',
                label="tiny",
                type_label="db",
                children=[
                    CatalogItem(
                        qualified_identifier='"tiny"."main"',
                        query_name='"tiny"."main"',
                        label="main",
                        type_label="sch",
                        children=[
                            CatalogItem(
                                qualified_identifier='"tiny"."main"."foo"',
                                query_name='"tiny"."main"."foo"',
                                label="foo",
                                type_label="t",
                                children=[
                                    CatalogItem(
                                        qualified_identifier='"tiny"."main"."foo"."foo_col"',
                                        query_name='"foo_col"',
                                        label="foo_col",
                                        type_label="#",
                                    )
                                ],
                            )
                        ],
                    )
                ],
            ),
        ]
    )
    assert get_catalog(conn) == expected


def test_init_script(tiny_db: Path, tmp_path: Path) -> None:
    script = (
        f".bail on\nselect \n1;\n.bail off\n.open {tiny_db}\n"
        "create table test_init as select 2;"
    )
    commands = _split_script(script)
    assert len(commands) == 5
    rewritten = [_rewrite_init_command(cmd) for cmd in commands]
    assert rewritten[0] == ""
    assert rewritten[1] == commands[1]
    assert rewritten[2] == ""
    assert rewritten[3].startswith(f"attach '{tiny_db}'")
    assert rewritten[4] == commands[4]

    conn = connect([":memory:"], init_script=(tmp_path, script))
    rel = conn.sql("select * from test_init")
    assert rel.fetchall() == [(2,)]
