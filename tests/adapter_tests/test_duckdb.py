from __future__ import annotations

import sys
from pathlib import Path

import pytest
from harlequin.catalog import Catalog, CatalogItem
from harlequin.exception import HarlequinConnectionError
from harlequin_duckdb.adapter import DuckDbAdapter


def test_connect(tiny_duck: Path, small_duck: Path, tmp_path: Path) -> None:
    tiny = str(tiny_duck)
    small = str(small_duck)
    assert DuckDbAdapter([], no_init=True).connect()
    assert DuckDbAdapter([":memory:"], no_init=True).connect()
    assert DuckDbAdapter([tiny], read_only=False, no_init=True).connect()
    assert DuckDbAdapter([tiny], read_only=True, no_init=True).connect()
    assert DuckDbAdapter(
        [tiny, str(Path(":memory:")), small], read_only=False, no_init=True
    ).connect()
    assert DuckDbAdapter([tiny, small], read_only=True, no_init=True).connect()
    assert DuckDbAdapter([str(tmp_path / "new.db")], no_init=True).connect()
    assert DuckDbAdapter([], allow_unsigned_extensions=True, no_init=True).connect()
    assert DuckDbAdapter([tiny], allow_unsigned_extensions=True, no_init=True).connect()
    assert DuckDbAdapter([tiny, small], read_only=True, no_init=True).connect()


@pytest.mark.online
def test_connect_extensions() -> None:
    assert DuckDbAdapter([], extension=None, no_init=True).connect()
    assert DuckDbAdapter([], extension=[], no_init=True).connect()
    assert DuckDbAdapter([], extension=["spatial"], no_init=True).connect()
    assert DuckDbAdapter(
        [], allow_unsigned_extensions=True, extension=["spatial"], no_init=True
    ).connect()


@pytest.mark.online
def test_connect_prql() -> None:
    # Note: this may fail in the future if the extension doesn't support the latest
    # duckdb version.
    assert DuckDbAdapter(
        [],
        allow_unsigned_extensions=True,
        extension=["prql"],
        custom_extension_repo="http://welsch.lu/duckdb/prql/latest",
        force_install_extensions=True,
    ).connect()


@pytest.mark.skipif(
    sys.version_info[0:2] != (3, 10), reason="Matrix is hitting MD too many times."
)
@pytest.mark.online
def test_connect_motherduck(tiny_duck: Path) -> None:
    # note: set environment variable motherduck_token
    assert DuckDbAdapter(["md:"], no_init=True)
    assert DuckDbAdapter(["md:cloudf1"], md_saas=True, no_init=True)
    assert DuckDbAdapter(["md:", str(tiny_duck)], no_init=True)


def test_cannot_connect(tiny_duck: Path) -> None:
    with pytest.raises(HarlequinConnectionError):
        DuckDbAdapter([":memory:"], read_only=True, no_init=True).connect()
    with pytest.raises(HarlequinConnectionError):
        DuckDbAdapter(
            [str(tiny_duck), ":memory:"], read_only=True, no_init=True
        ).connect()


def test_get_databases(tiny_duck: Path, small_duck: Path) -> None:
    conn = DuckDbAdapter([str(tiny_duck), str(small_duck)], no_init=True).connect()
    assert conn._get_databases() == [("small",), ("tiny",)]


def test_get_schemas(small_duck: Path) -> None:
    conn = DuckDbAdapter([str(small_duck)], read_only=True, no_init=True).connect()
    assert conn._get_schemas("small") == [("empty",), ("main",)]


def test_get_tables(small_duck: Path) -> None:
    conn = DuckDbAdapter([str(small_duck)], read_only=True, no_init=True).connect()
    assert conn._get_tables("small", "empty") == []
    assert conn._get_tables("small", "main") == [("drivers", "BASE TABLE")]


def test_get_columns(small_duck: Path) -> None:
    conn = DuckDbAdapter([str(small_duck)], read_only=True, no_init=True).connect()
    assert conn._get_columns("small", "main", "drivers") == [
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


def test_get_catalog(tiny_duck: Path, small_duck: Path) -> None:
    conn = DuckDbAdapter(
        [str(tiny_duck), str(small_duck)], read_only=True, no_init=True
    ).connect()
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
    assert conn.get_catalog() == expected


def test_init_script(tiny_duck: Path, tmp_path: Path) -> None:
    script = (
        f".bail on\nselect \n1;\n.bail off\n.open {tiny_duck}\n"
        "create table test_init as select 2;"
    )
    commands = DuckDbAdapter._split_script(script)
    assert len(commands) == 5
    rewritten = [DuckDbAdapter._rewrite_init_command(cmd) for cmd in commands]
    assert rewritten[0] == ""
    assert rewritten[1] == commands[1]
    assert rewritten[2] == ""
    assert rewritten[3].startswith(f"attach '{tiny_duck}'")
    assert rewritten[4] == commands[4]

    with open(tmp_path / "myscript", "w") as f:
        f.write(script)

    conn = DuckDbAdapter([":memory:"], init_path=tmp_path / "myscript").connect()
    cur = conn.execute("select * from test_init")
    assert cur
    assert cur.relation.fetchall() == [(2,)]


def test_initialize_adapter_ignores_extra_kwargs() -> None:
    adapter = DuckDbAdapter((":memory:",), foo="bar")
    assert adapter


def test_transaction_mode() -> None:
    adapter = DuckDbAdapter((":memory:",))
    conn = adapter.connect()
    assert conn.transaction_mode is None
    assert conn.toggle_transaction_mode() is None
    assert conn.transaction_mode is None
