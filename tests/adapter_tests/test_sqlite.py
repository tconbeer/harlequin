from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from harlequin.catalog import Catalog, CatalogItem, InteractiveCatalogItem
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
    catalog = conn.get_catalog()
    assert [item.label for item in catalog.items] == [
        item.label for item in expected.items
    ]
    for i, database_item in enumerate(catalog.items):
        assert isinstance(database_item, InteractiveCatalogItem)
        assert database_item.children == []
        schema_items = database_item.fetch_children()
        assert [item.label for item in schema_items] == [
            item.label for item in expected.items[i].children
        ]
        for j, schema_item in enumerate(schema_items):
            assert isinstance(schema_item, InteractiveCatalogItem)
            assert schema_item.children == []
            relation_items = schema_item.fetch_children()
            assert [(item.label, item.type_label) for item in relation_items] == [
                (item.label, item.type_label)
                for item in expected.items[i].children[j].children
            ]
            for k, relation_item in enumerate(relation_items):
                assert isinstance(relation_item, InteractiveCatalogItem)
                assert relation_item.children == []
                column_items = relation_item.fetch_children()
                assert [(item.label, item.type_label) for item in column_items] == [
                    (item.label, item.type_label)
                    for item in expected.items[i].children[j].children[k].children
                ]


def test_catalog_items_carry_sqlites_own_type_names(tmp_path: Path) -> None:
    """The declared type, which sqlite keeps verbatim -- and None for a column
    declared without one, since an empty string is not a type."""
    conn = HarlequinSqliteAdapter([str(tmp_path / "types.sqlite")]).connect()
    conn.execute("create table t (id bigint, note, total decimal(18,2))")
    conn.execute("create view v as select 1 as n")

    (database_item,) = conn.get_catalog().items
    assert isinstance(database_item, InteractiveCatalogItem)
    assert database_item.type_name == "database"

    relation_items = database_item.fetch_children()
    assert [(item.label, item.type_name) for item in relation_items] == [
        ("t", "table"),
        ("v", "view"),
    ]

    table_item = relation_items[0]
    assert isinstance(table_item, InteractiveCatalogItem)
    assert [
        (item.label, item.type_label, item.type_name)
        for item in table_item.fetch_children()
    ] == [
        ("id", "##", "bigint"),
        ("note", "#.#", None),
        ("total", "#.#", "decimal(18,2)"),
    ]


def test_search_catalog_finds_relations_and_columns(tmp_path: Path) -> None:
    """One query per attached database, rather than one per relation, and every
    match carries the path that reaches it."""
    conn = HarlequinSqliteAdapter([str(tmp_path / "search.sqlite")]).connect()
    conn.execute("create table orders (id bigint, customer_id bigint)")
    conn.execute("create view order_summary as select 1 as n")
    conn.execute("create table customers (customer_id bigint)")

    assert [
        (result.parents, result.item.label, result.item.type_name)
        for result in conn.search_catalog("ORDER")
    ] == [
        (("main",), "order_summary", "view"),
        (("main",), "orders", "table"),
    ]
    assert [
        (result.parents, result.item.label, result.item.type_name)
        for result in conn.search_catalog("customer_id")
    ] == [
        (("main", "customers"), "customer_id", "bigint"),
        (("main", "orders"), "customer_id", "bigint"),
    ]


def test_search_catalog_takes_one_kind_at_a_time(tmp_path: Path) -> None:
    conn = HarlequinSqliteAdapter([str(tmp_path / "kinds.sqlite")]).connect()
    conn.execute("create table orders (orders bigint)")

    assert [
        result.item.type_label for result in conn.search_catalog("orders", "relations")
    ] == ["t"]
    assert [
        result.item.type_label for result in conn.search_catalog("orders", "columns")
    ] == ["##"]
    assert len(conn.search_catalog("orders", "all")) == 2


def test_search_catalog_reads_a_wildcard_as_a_character(tmp_path: Path) -> None:
    """The term is a substring, so a LIKE metacharacter in it matches itself."""
    conn = HarlequinSqliteAdapter([str(tmp_path / "wild.sqlite")]).connect()
    conn.execute('create table "a%b" (n bigint)')
    conn.execute("create table ab (n bigint)")

    assert [result.item.label for result in conn.search_catalog("a%b")] == ["a%b"]
    assert [result.item.label for result in conn.search_catalog("a_b")] == []


def test_search_catalog_covers_every_attached_database(
    tiny_sqlite: Path, small_sqlite: Path
) -> None:
    conn = HarlequinSqliteAdapter([str(tiny_sqlite), str(small_sqlite)]).connect()
    found = conn.search_catalog("")
    assert {result.parents[0] for result in found if result.parents} == {
        "main",
        "small",
    }


def test_search_catalog_matches_every_level(tmp_path: Path) -> None:
    """A caller searching a catalog does not know its shape, so an attached
    database named like the term is an answer too. SQLite has no schema between
    a database and its relations, which is why this path is one level shorter
    than duckdb's.

    Grouped by attached database, since that is what SQLite asks one query per,
    and within each one an item arrives before the items under it.
    """
    database = tmp_path / "sales.sqlite"
    conn = HarlequinSqliteAdapter([str(database)]).connect()
    conn.execute("create table sales (sales bigint)")
    conn.execute(f"attach database '{database}' as sales")

    assert [
        (result.parents, result.item.label, result.item.type_label)
        for result in conn.search_catalog("sales")
    ] == [
        (("main",), "sales", "t"),
        (("main", "sales"), "sales", "##"),
        ((), "sales", "db"),
        (("sales",), "sales", "t"),
        (("sales", "sales"), "sales", "##"),
    ]


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


def test_a_query_with_no_rows_still_returns_its_columns() -> None:
    """A caller handed `None` cannot know what the query selected, so an export
    or a headless render would lose the header. Arrow rather than a dict of
    columns, so duplicate names survive."""
    import pyarrow as pa

    conn = HarlequinSqliteAdapter((":memory:",), no_init=True).connect()
    cur = conn.execute("select 1 as a, 'x' as b where 1=0")
    assert cur is not None
    assert cur.columns() == [("a", "?"), ("b", "?")]

    data = cur.fetchall()
    assert isinstance(data, pa.Table)
    assert data.column_names == ["a", "b"]
    assert data.num_rows == 0


def test_duplicate_column_names_survive_an_empty_result() -> None:
    import pyarrow as pa

    conn = HarlequinSqliteAdapter((":memory:",), no_init=True).connect()
    cur = conn.execute("select 1 as a, 2 as a where 1=0")
    assert cur is not None
    data = cur.fetchall()
    assert isinstance(data, pa.Table)
    assert data.column_names == ["a", "a"]


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


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_a_tiny_limit(small_sqlite: Path, limit: int) -> None:
    """The first row is fetched eagerly, and 0 and 1 are where that shows.

    `fetchmany(0)` reads as "all of them" in sqlite3, so a limit of one row is
    a call that must not be made -- and a limit of no rows is a header.
    """
    conn = HarlequinSqliteAdapter((str(small_sqlite),)).connect()
    cur = conn.execute("select * from drivers")
    assert cur
    results = cur.set_limit(limit).fetchall()
    assert results is not None
    assert len(results) == limit


@pytest.mark.py12
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
