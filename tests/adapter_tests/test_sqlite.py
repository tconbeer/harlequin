from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from harlequin.catalog import Catalog, CatalogItem
from harlequin.exception import HarlequinConfigError, HarlequinConnectionError
from harlequin_sqlite import HarlequinSqliteAdapter


@pytest.fixture
def extension_path(data_dir: Path) -> Path:
    return data_dir / "adapter_tests" / "sqlite" / "extensions" / "hello0"


def test_connect(tiny_sqlite: Path, small_sqlite: Path) -> None:
    tiny = str(tiny_sqlite)
    small = str(small_sqlite)
    assert HarlequinSqliteAdapter([]).connect()
    assert HarlequinSqliteAdapter([":memory:"]).connect()
    assert HarlequinSqliteAdapter([tiny], read_only=False).connect()
    assert HarlequinSqliteAdapter([tiny], read_only=True).connect()
    assert HarlequinSqliteAdapter([tiny], connection_mode="ro").connect()
    assert HarlequinSqliteAdapter([tiny, small, ":memory:"], read_only=False).connect()
    assert HarlequinSqliteAdapter(
        [],
        read_only=False,
        timeout="100",
        isolation_level="EXCLUSIVE",
        check_same_thread=False,
        cached_statements="10",
    )


def test_cannot_connect(tmp_path: Path, tiny_sqlite: Path) -> None:
    nonexistent_db = tmp_path / "no.db"
    with pytest.raises(HarlequinConnectionError):
        HarlequinSqliteAdapter((str(nonexistent_db),), read_only=True).connect()
    with pytest.raises(HarlequinConnectionError):
        HarlequinSqliteAdapter(
            (str(nonexistent_db), ":memory:"), read_only=True
        ).connect()
    with pytest.raises(HarlequinConnectionError):
        HarlequinSqliteAdapter(
            (str(tiny_sqlite),), read_only=True, connection_mode="rwc"
        ).connect()


def test_get_databases(tiny_sqlite: Path, tmp_path: Path) -> None:
    new_db = tmp_path / "new.db"
    conn = HarlequinSqliteAdapter((str(tiny_sqlite), str(new_db))).connect()
    assert conn._get_databases() == ["main", "new"]


def test_get_tables(tiny_sqlite: Path, small_sqlite: Path) -> None:
    conn = HarlequinSqliteAdapter(
        [str(tiny_sqlite), str(small_sqlite)], read_only=True
    ).connect()
    assert conn._get_relations("main") == [("foo", "table")]
    assert conn._get_relations("small") == [("drivers", "table")]


def test_get_columns(small_sqlite: Path) -> None:
    conn = HarlequinSqliteAdapter([str(small_sqlite)], read_only=True).connect()
    cols = conn._get_columns(db_name="main", rel_name="drivers")
    assert [(col_name, col_type) for _, col_name, col_type, *_ in cols] == [
        ("driverId", "BIGINT"),
        ("driverRef", "VARCHAR"),
        ("number", "VARCHAR"),
        ("code", "VARCHAR"),
        ("forename", "VARCHAR"),
        ("surname", "VARCHAR"),
        ("dob", "VARCHAR"),
        ("nationality", "VARCHAR"),
        ("url", "VARCHAR"),
    ]


def test_get_catalog(tiny_sqlite: Path, small_sqlite: Path) -> None:
    conn = HarlequinSqliteAdapter(
        [str(tiny_sqlite), str(small_sqlite)], read_only=True
    ).connect()
    expected = Catalog(
        items=[
            CatalogItem(
                qualified_identifier='"main"',
                query_name='"main"',
                label="main",
                type_label="db",
                children=[
                    CatalogItem(
                        qualified_identifier='"main"."foo"',
                        query_name='"main"."foo"',
                        label="foo",
                        type_label="t",
                        children=[
                            CatalogItem(
                                qualified_identifier='"main"."foo"."foo_col"',
                                query_name='"foo_col"',
                                label="foo_col",
                                type_label="##",
                            )
                        ],
                    )
                ],
            ),
            CatalogItem(
                qualified_identifier='"small"',
                query_name='"small"',
                label="small",
                type_label="db",
                children=[
                    CatalogItem(
                        qualified_identifier='"small"."drivers"',
                        query_name='"small"."drivers"',
                        label="drivers",
                        type_label="t",
                        children=[
                            CatalogItem(
                                qualified_identifier='"small"."drivers"."driverId"',
                                query_name='"driverId"',
                                label="driverId",
                                type_label="##",
                            ),
                            CatalogItem(
                                qualified_identifier='"small"."drivers"."driverRef"',
                                query_name='"driverRef"',
                                label="driverRef",
                                type_label="s",
                            ),
                            CatalogItem(
                                qualified_identifier='"small"."drivers"."number"',
                                query_name='"number"',
                                label="number",
                                type_label="s",
                            ),
                            CatalogItem(
                                qualified_identifier='"small"."drivers"."code"',
                                query_name='"code"',
                                label="code",
                                type_label="s",
                            ),
                            CatalogItem(
                                qualified_identifier='"small"."drivers"."forename"',
                                query_name='"forename"',
                                label="forename",
                                type_label="s",
                            ),
                            CatalogItem(
                                qualified_identifier='"small"."drivers"."surname"',
                                query_name='"surname"',
                                label="surname",
                                type_label="s",
                            ),
                            CatalogItem(
                                qualified_identifier='"small"."drivers"."dob"',
                                query_name='"dob"',
                                label="dob",
                                type_label="s",
                            ),
                            CatalogItem(
                                qualified_identifier='"small"."drivers"."nationality"',
                                query_name='"nationality"',
                                label="nationality",
                                type_label="s",
                            ),
                            CatalogItem(
                                qualified_identifier='"small"."drivers"."url"',
                                query_name='"url"',
                                label="url",
                                type_label="s",
                            ),
                        ],
                    )
                ],
            ),
        ]
    )
    assert conn.get_catalog() == expected


def test_init_script(tiny_sqlite: Path, tmp_path: Path) -> None:
    script = (
        f".bail on\nselect \n1;\n.bail off\n.open {tiny_sqlite}\n"
        "create table test_init as select 2;"
    )
    commands = HarlequinSqliteAdapter._split_script(script)
    assert len(commands) == 5
    rewritten = [HarlequinSqliteAdapter._rewrite_init_command(cmd) for cmd in commands]
    assert rewritten[0] == ""
    assert rewritten[1] == commands[1]
    assert rewritten[2] == ""
    assert rewritten[3].startswith(f"attach '{tiny_sqlite}'")
    assert rewritten[4] == commands[4]

    with open(tmp_path / "myscript", "w") as f:
        f.write(script)

    conn = HarlequinSqliteAdapter(
        [":memory:"], init_path=tmp_path / "myscript"
    ).connect()
    cur = conn.execute("select * from test_init")
    assert cur
    assert cur.fetchall() == [(2,)]


def test_rewrite_load(extension_path: Path) -> None:
    cmd = f".load {extension_path.as_posix()}"
    rewritten = HarlequinSqliteAdapter._rewrite_init_command(cmd)
    assert rewritten.startswith("select load_extension")


@pytest.mark.skipif(
    not hasattr(sqlite3.Connection, "enable_load_extension"),
    reason="Not supported on many Pythons.",
)
def test_load_extension(extension_path: Path) -> None:
    conn = HarlequinSqliteAdapter(
        [":memory:"], extension=[extension_path.as_posix()]
    ).connect()
    assert conn


@pytest.mark.skipif(
    hasattr(sqlite3.Connection, "enable_load_extension"),
    reason="Not supported on many Pythons.",
)
def test_load_extension_raises(extension_path: Path) -> None:
    with pytest.raises(HarlequinConfigError) as exc_info:
        _ = HarlequinSqliteAdapter(
            [":memory:"], extension=[extension_path.as_posix()]
        ).connect()
    assert "harlequin.sh" in str(exc_info)


def test_initialize_adapter_ignores_extra_kwargs() -> None:
    adapter = HarlequinSqliteAdapter((":memory:",), foo="bar")
    assert adapter
    assert adapter.connect()


def test_limit(small_sqlite: Path) -> None:
    adapter = HarlequinSqliteAdapter((str(small_sqlite),))
    conn = adapter.connect()
    cur = conn.execute("select * from drivers")
    assert cur
    results = cur.fetchall()
    assert len(results) == 857  # type: ignore

    cur = conn.execute("select * from drivers")
    assert cur
    cur = cur.set_limit(100)
    results = cur.fetchall()
    assert len(results) == 100  # type: ignore


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="Transactions only supported on py3.12+"
)
def test_transaction_mode() -> None:
    adapter = HarlequinSqliteAdapter((":memory:",))
    conn = adapter.connect()
    assert conn.transaction_mode is not None
    assert conn.transaction_mode.label == "Auto"
    assert conn.transaction_mode.commit is None
    assert conn.transaction_mode.rollback is None
    new_mode = conn.toggle_transaction_mode()
    assert new_mode
    assert new_mode.label == "Manual"
    assert new_mode.commit is not None
    assert new_mode.rollback is not None
    assert conn.transaction_mode.label == "Manual"
    assert conn.toggle_transaction_mode()
    assert conn.transaction_mode.label == "Auto"
