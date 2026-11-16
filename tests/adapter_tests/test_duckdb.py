from __future__ import annotations

import sys
import threading
from pathlib import Path

import duckdb
import pytest

from harlequin.catalog import Catalog, CatalogItem, InteractiveCatalogItem
from harlequin.exception import HarlequinConnectionError, HarlequinQueryError
from harlequin.statements import split
from harlequin_duckdb.adapter import DuckDbAdapter, DuckDbConnection
from harlequin_duckdb.catalog import (
    DatabaseCatalogItem,
    RelationCatalogItem,
    SchemaCatalogItem,
)
from harlequin_duckdb.interactions import (
    execute_drop_schema_statement,
    execute_drop_table_statement,
    execute_drop_view_statement,
    execute_use_statement,
    insert_columns_at_cursor,
    show_describe_relation,
    show_export_database,
    show_select_star,
    show_summarize_relation,
    show_table_ddl,
    show_view_ddl,
)
from harlequin_duckdb.quoting import qualified_name, quote_identifier, quote_literal
from tests.recording_driver import RecordingDriver
from tests.waiting import wait_until


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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="PRQL not supported on windows with this duckdb version.",
)
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


def test_catalog_items_carry_duckdbs_own_type_names(tmp_path: Path) -> None:
    """`type_name` is the full type duckdb spells, which a 1-3 character
    `type_label` cannot carry."""
    conn = DuckDbAdapter([str(tmp_path / "types.db")], no_init=True).connect()
    conn.execute("create table t (id bigint, total decimal(18,2))")
    conn.execute("create view v as select 1 as n")

    (database_item,) = conn.get_catalog().items
    assert isinstance(database_item, InteractiveCatalogItem)
    assert database_item.type_name == "database"
    (schema_item,) = database_item.fetch_children()
    assert isinstance(schema_item, InteractiveCatalogItem)
    assert schema_item.type_name == "schema"

    relation_items = schema_item.fetch_children()
    assert [(item.label, item.type_name) for item in relation_items] == [
        ("t", "BASE TABLE"),
        ("v", "VIEW"),
    ]

    table_item = relation_items[0]
    assert isinstance(table_item, InteractiveCatalogItem)
    assert [
        (item.label, item.type_label, item.type_name)
        for item in table_item.fetch_children()
    ] == [("id", "##", "BIGINT"), ("total", "#.#", "DECIMAL(18,2)")]


def test_search_catalog_finds_relations_and_columns(tmp_path: Path) -> None:
    """One query answers what a walk would have paid a round trip a level for,
    and every match carries the path that reaches it."""
    conn = DuckDbAdapter([str(tmp_path / "search.db")], no_init=True).connect()
    conn.execute("create schema analytics")
    conn.execute("create table analytics.orders (id bigint, customer_id bigint)")
    conn.execute("create view analytics.order_summary as select 1 as n")
    conn.execute("create table main.customers (customer_id bigint)")

    assert [
        (result.parents, result.item.label, result.item.type_name)
        for result in conn.search_catalog("ORDER")
    ] == [
        (("search", "analytics"), "order_summary", "VIEW"),
        (("search", "analytics"), "orders", "BASE TABLE"),
    ]
    assert [
        (result.parents, result.item.label, result.item.type_name)
        for result in conn.search_catalog("customer_id")
    ] == [
        (("search", "analytics", "orders"), "customer_id", "BIGINT"),
        (("search", "main", "customers"), "customer_id", "BIGINT"),
    ]


def test_search_catalog_matches_every_level(tmp_path: Path) -> None:
    """A caller searching a catalog does not know its shape, so a database or a
    schema named like the term is an answer too -- and each arrives before the
    items under it."""
    conn = DuckDbAdapter([str(tmp_path / "sales.db")], no_init=True).connect()
    conn.execute("create schema sales_eu")
    conn.execute("create table sales_eu.sales (sales bigint)")

    assert [
        (result.parents, result.item.label, result.item.type_label)
        for result in conn.search_catalog("sales")
    ] == [
        ((), "sales", "db"),
        (("sales",), "sales_eu", "sch"),
        (("sales", "sales_eu"), "sales", "t"),
        (("sales", "sales_eu", "sales"), "sales", "##"),
    ]


def test_search_catalog_takes_one_kind_at_a_time(tmp_path: Path) -> None:
    conn = DuckDbAdapter([str(tmp_path / "kinds.db")], no_init=True).connect()
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
    conn = DuckDbAdapter([str(tmp_path / "wild.db")], no_init=True).connect()
    conn.execute('create table "a%b" (n bigint)')
    conn.execute("create table ab (n bigint)")

    assert [result.item.label for result in conn.search_catalog("a%b")] == ["a%b"]
    assert [result.item.label for result in conn.search_catalog("a_b")] == []


def test_search_catalog_reports_only_paths_the_catalog_walks(tmp_path: Path) -> None:
    """duckdb's temp catalog holds objects the catalog tree never shows, and a
    path a listing cannot reach is not a path worth printing."""
    conn = DuckDbAdapter([str(tmp_path / "temp.db")], no_init=True).connect()
    conn.execute("create temp table tmp_orders (n bigint)")

    assert conn.search_catalog("tmp_orders") == []


def test_init_script(tiny_duck: Path, tmp_path: Path) -> None:
    script = (
        f".bail on\nselect \n1;\n.bail off\n.open {tiny_duck}\n"
        "create table test_init as select 2;"
    )
    commands = DuckDbAdapter._split_script(script)
    assert len(commands) == 5
    rewritten = [
        DuckDbAdapter._rewrite_init_command(cmd, duckdb.connect()) for cmd in commands
    ]
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


@pytest.mark.parametrize("primary_is_in_memory", [True, False])
def test_a_bare_open_switches_to_a_fresh_in_memory_database(
    tmp_path: Path, primary_is_in_memory: bool
) -> None:
    """As it does in the duckdb CLI, whatever the primary database is called."""
    script_path = tmp_path / "myscript"
    script_path.write_text(
        "create table before_open as select 1;\n"
        ".open\n"
        "create table after_first_open as select 2;\n"
        ".open\n"
        "create table after_second_open as select 3;\n"
    )
    primary = ":memory:" if primary_is_in_memory else str(tmp_path / "primary.db")
    conn = DuckDbAdapter([primary], init_path=script_path).connect()

    [(current_database,)] = conn.conn.sql("select current_database()").fetchall()
    database_by_table = dict(
        conn.conn.sql(
            "select table_name, database_name from duckdb_tables()"
        ).fetchall()
    )
    primary_name = "memory" if primary_is_in_memory else "primary"
    assert database_by_table["before_open"] == primary_name
    assert database_by_table["after_second_open"] == current_database
    assert len(set(database_by_table.values())) == 3


def test_initialize_adapter_ignores_extra_kwargs() -> None:
    adapter = DuckDbAdapter((":memory:",), foo="bar")
    assert adapter


def test_transaction_mode() -> None:
    adapter = DuckDbAdapter((":memory:",))
    conn = adapter.connect()
    assert conn.transaction_mode is None
    assert conn.toggle_transaction_mode() is None
    assert conn.transaction_mode is None


@pytest.mark.parametrize(
    ("native_type", "expected"),
    [
        ("GEOMETRY", "geo"),
        ("GEOMETRY[]", "[geo]"),
        ("POINT_2D", "geo"),
        ("POINT_3D", "geo"),
        ("POINT_4D", "geo"),
        ("LINESTRING_2D", "geo"),
        ("LINESTRING_3D", "geo"),
        ("POLYGON_2D", "geo"),
        ("POLYGON_3D", "geo"),
        ("BOX_2D", "geo"),
        ("BOX_2DF", "geo"),
        ("WKB_BLOB", "0b"),
        ("BLOB", "0b"),
    ],
)
def test_spatial_types_have_a_label(native_type: str, expected: str) -> None:
    """Typed as strings, so this needs no spatial extension to be installed."""
    assert DuckDbConnection._short_column_type(native_type) == expected


@pytest.mark.parametrize(
    ("native_type", "expected"),
    [
        ('"NULL"', "\\n"),
        ('"NULL"[]', "[\\n]"),
        ("TIMESTAMPTZ_NS", "ttz"),
        ("TUPLE(INTEGER, INTEGER)", "{}"),
        ("VARIANT", "var"),
    ],
)
def test_duckdb_2_types_have_a_label(native_type: str, expected: str) -> None:
    assert DuckDbConnection._short_column_type(native_type) == expected


@pytest.mark.parametrize(
    ("native_type", "expected"),
    [
        ("ENUM('a', 'b')", "enum"),
        ("ENUM('a', 'b')[]", "[enum]"),
        ("JSON", "{j}"),
        ("UHUGEINT", "u###"),
        ("BIGNUM", "###"),
        ("VARINT", "###"),
        ("TIME_NS", "t"),
        ("TIME WITH TIME ZONE", "ttz"),
        ("UNION(a INTEGER, b VARCHAR)", "uni"),
        ("INTEGER[3]", "[#]"),
        ("STRUCT(a INTEGER)[2]", "[{}]"),
        ("INTEGER[][]", "[#]"),
    ],
)
def test_more_types_have_a_label(native_type: str, expected: str) -> None:
    assert DuckDbConnection._short_column_type(native_type) == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("'a'::enum('a', 'b')", "enum"),
        ("'{}'::json", "{j}"),
        ("1::uhugeint", "u###"),
        ("1::varint", "###"),
        ("'01:02:03'::time_ns", "t"),
        ("'01:02:03+01'::timetz", "ttz"),
        ("now()::timestamp_s", "ts"),
        ("now()::timestamp_ms", "ts"),
        ("union_value(a := 1)::union(a int, b varchar)", "uni"),
        ("[1, 2, 3]::int[3]", "[#]"),
    ],
)
def test_every_type_a_query_returns_has_a_label(expression: str, expected: str) -> None:
    """Spelled by the installed duckdb, not by hand."""
    conn = DuckDbAdapter([":memory:"], no_init=True).connect()
    try:
        cur = conn.execute(f"select {expression} as x")
    except HarlequinQueryError as e:
        if "Type with name" not in str(e):
            raise
        pytest.skip(f"this duckdb has no type for {expression}")
    assert cur is not None
    assert cur.columns() == [("x", expected)]


@pytest.mark.parametrize(
    "statement",
    [
        "create table t2 (a int)",
        "create table t2 as select 1 as a",
        "insert into t values (2)",
        "update t set a = 3",
        "delete from t",
        "use memory",
        "set threads = 1",
        "attach ':memory:' as other",
        "begin",
    ],
)
def test_a_statement_that_returns_no_rows_has_no_cursor(statement: str) -> None:
    conn = DuckDbAdapter([":memory:"], no_init=True).connect()
    conn.conn.execute("create table t as select 1 as a")
    assert conn.execute(statement) is None


def test_a_statement_returning_rows_has_a_cursor_even_if_it_writes() -> None:
    conn = DuckDbAdapter([":memory:"], no_init=True).connect()
    conn.conn.execute("create table t (a bigint)")
    cur = conn.execute('insert into t values (7) returning a as "Count"')
    assert cur is not None
    assert cur.relation.fetchall() == [(7,)]


def test_fetchone() -> None:
    conn = DuckDbAdapter([":memory:"], no_init=True).connect()
    cur = conn.execute("select * from range(5) t(i) order by i")
    assert cur is not None
    assert cur.fetchone() == (0,)
    empty = conn.execute("select 1 where false")
    assert empty is not None
    assert empty.fetchone() is None


def test_validate_sql_leaves_the_connection_usable() -> None:
    conn = DuckDbAdapter([":memory:"], no_init=True).connect()
    assert conn.validate_sql("select 1") == "select 1"
    assert conn.validate_sql("selec 1") == ""
    cur = conn.execute("select 2")
    assert cur is not None
    assert cur.relation.fetchall() == [(2,)]


def test_an_init_script_that_ends_in_a_query_leaves_the_connection_usable(
    tmp_path: Path,
) -> None:
    (tmp_path / "myscript").write_text("select * from range(100000);\n")
    conn = DuckDbAdapter([":memory:"], init_path=tmp_path / "myscript").connect()
    cur = conn.execute("select 1")
    assert cur is not None
    assert cur.relation.fetchall() == [(1,)]


@pytest.mark.skipif(
    not hasattr(duckdb, "ResourceInUseException"),
    reason="before duckdb 2, a busy connection is not refused",
)
def test_a_query_submitted_while_one_runs_interrupts_it() -> None:
    conn = DuckDbAdapter([":memory:"], no_init=True).connect()
    # query_progress() is -1 unless a query is executing, which binding one is not
    conn.conn.execute(
        "set enable_progress_bar = true;"
        "set enable_progress_bar_print = false;"
        "set progress_bar_time = 0"
    ).fetchall()
    slow_results: list[object] = []

    def run_slow_query() -> None:
        cursor = conn.execute(
            "select count(*) from range(100_000_000_000) t(i) where i % 7 = 3"
        )
        slow_results.append(None if cursor is None else cursor.fetchall())

    slow_query = threading.Thread(target=run_slow_query, daemon=True)
    slow_query.start()
    try:
        wait_until(
            lambda: conn.conn.query_progress() >= 0,
            description="the slow query to be executing",
        )
        cursor = conn.execute("select 42")
        assert cursor is not None
        assert cursor.relation.fetchall() == [(42,)]
    finally:
        # so a failure above does not leave the slow query running
        wait_until(
            lambda: _cancelled(conn, slow_query),
            description="the slow query to stop",
        )
    # an interrupted fetch returns no data
    assert slow_results == [None]


def _cancelled(connection: DuckDbConnection, thread: threading.Thread) -> bool:
    """Interrupt whatever runs on the connection; True once `thread` has ended."""
    if thread.is_alive():
        connection.cancel()
    return not thread.is_alive()


def test_every_statement_runs_before_any_is_fetched() -> None:
    """The app executes a whole script, then fetches each cursor, in that order.

    A statement that is not a SELECT but returns rows runs when it is executed,
    so its rows must not hold the connection against the statements after it.
    """
    conn = DuckDbAdapter([":memory:"], no_init=True).connect()
    statements = [
        "create table t (a bigint)",
        "insert into t select * from range(100000) returning a",
        "pragma version",
        "explain select 1",
        "delete from t where a >= 10 returning a",
        "select count(*) from t",
    ]
    cursors = [conn.execute(statement) for statement in statements]
    assert cursors[0] is None
    returning, version, plan, deleted, count = cursors[1:]
    assert returning is not None and len(returning.relation.fetchall()) == 100000
    assert version is not None and version.relation.fetchall()
    assert plan is not None and plan.relation.fetchall()
    assert deleted is not None and len(deleted.relation.fetchall()) == 99990
    assert count is not None and count.relation.fetchall() == [(10,)]


ODD_DATABASE = 'Bob\'s "db";x'
ODD_SCHEMA = 'a.b "s";c'
ODD_RELATIONS = [
    "Bob's orders",
    'say "hi"',
    "a.b",
    "semi;colon",
    "back\\slash",
    "tick`s",
    "brack]et",
    "Mixed Case",
    "select",
    "2024-sales",
]
"""Table names that each break a hand-quoted identifier or literal somewhere."""


@pytest.fixture
def odd_names(tmp_path: Path) -> DuckDbConnection:
    """An attached database and a schema with awkward names, holding a table
    for each of ODD_RELATIONS, with one column and one row both named like the
    table, and a view over each called `v <name>`."""
    conn = DuckDbAdapter([str(tmp_path / "main.db")], no_init=True).connect()
    other_path = quote_literal(str(tmp_path / "other.db"))
    conn.conn.execute(f"attach {other_path} as {quote_identifier(ODD_DATABASE)}")
    conn.conn.execute(f"create schema {qualified_name(ODD_DATABASE, ODD_SCHEMA)}")
    for name in ODD_RELATIONS:
        table = qualified_name(ODD_DATABASE, ODD_SCHEMA, name)
        view = qualified_name(ODD_DATABASE, ODD_SCHEMA, f"v {name}")
        conn.conn.execute(f"create table {table} ({quote_identifier(name)} varchar)")
        conn.conn.execute(f"insert into {table} values (?)", [name])
        conn.conn.execute(f"create view {view} as select * from {table}")
    return conn


def _odd_schema(conn: DuckDbConnection) -> SchemaCatalogItem:
    (database_item,) = [
        item for item in conn.get_catalog().items if item.label == ODD_DATABASE
    ]
    assert isinstance(database_item, DatabaseCatalogItem)
    (schema_item,) = [
        item for item in database_item.fetch_children() if item.label == ODD_SCHEMA
    ]
    return schema_item


def _odd_relations(conn: DuckDbConnection) -> dict[str, RelationCatalogItem]:
    return {
        relation_item.label: relation_item
        for relation_item in _odd_schema(conn).fetch_children()
    }


def _rows(conn: DuckDbConnection, query: str) -> list[tuple]:
    cursor = conn.execute(query)
    assert cursor is not None
    return cursor.relation.fetchall()


def _table_name(relation_label: str) -> str:
    return relation_label.removeprefix("v ")


def test_catalog_loads_names_that_need_quoting(odd_names: DuckDbConnection) -> None:
    relation_items = _odd_relations(odd_names)
    assert sorted(relation_items) == sorted(
        [*ODD_RELATIONS, *(f"v {name}" for name in ODD_RELATIONS)]
    )
    for label, relation_item in relation_items.items():
        assert relation_item.qualified_identifier == qualified_name(
            ODD_DATABASE, ODD_SCHEMA, label
        )
        (column_item,) = relation_item.fetch_children()
        assert column_item.label == _table_name(label)
        assert column_item.query_name == quote_identifier(_table_name(label))


def test_search_catalog_finds_names_that_need_quoting(
    odd_names: DuckDbConnection,
) -> None:
    for name in ODD_RELATIONS:
        found = [
            result.item.qualified_identifier
            for result in odd_names.search_catalog(name, "relations")
        ]
        assert qualified_name(ODD_DATABASE, ODD_SCHEMA, name) in found
        relation_item = _odd_relations(odd_names)[name]
        assert _rows(odd_names, f"select * from {found[0]}") == _rows(
            odd_names, f"select * from {relation_item.qualified_identifier}"
        )


def test_every_relation_interaction_targets_its_own_relation(
    odd_names: DuckDbConnection,
) -> None:
    for label, relation_item in _odd_relations(odd_names).items():
        table_name = _table_name(label)
        driver = RecordingDriver()
        show_select_star(relation_item, driver)
        show_describe_relation(relation_item, driver)
        show_summarize_relation(relation_item, driver)
        insert_columns_at_cursor(relation_item, driver)
        select_star, describe, summarize = driver.new_buffers
        (columns,) = driver.inserted_at_selection

        assert _rows(odd_names, select_star) == [(table_name,)]
        assert [row[0] for row in _rows(odd_names, describe)] == [table_name]
        assert [row[0] for row in _rows(odd_names, summarize)] == [table_name]
        assert _rows(
            odd_names, f"select {columns} from {relation_item.qualified_identifier}"
        ) == [(table_name,)]


def test_show_ddl_reads_the_relation_it_was_asked_about(
    odd_names: DuckDbConnection,
) -> None:
    for label, relation_item in _odd_relations(odd_names).items():
        driver = RecordingDriver()
        if label.startswith("v "):
            show_view_ddl(relation_item, driver)
            kind = "VIEW"
        else:
            show_table_ddl(relation_item, driver)
            kind = "TABLE"
        (ddl,) = driver.new_buffers
        assert ddl.startswith(f"CREATE {kind} {qualified_name(ODD_SCHEMA, label)}")


def test_use_switches_to_a_database_and_schema_that_need_quoting(
    odd_names: DuckDbConnection,
) -> None:
    schema_item = _odd_schema(odd_names)
    assert schema_item.parent is not None
    execute_use_statement(schema_item.parent, RecordingDriver())
    assert _rows(odd_names, "select current_database()") == [(ODD_DATABASE,)]
    execute_use_statement(schema_item, RecordingDriver())
    assert _rows(odd_names, "select current_schema()") == [(ODD_SCHEMA,)]

    driver = RecordingDriver()
    show_export_database(schema_item.parent, driver)
    (export_script,) = driver.new_buffers
    use_statement = split(export_script)[0].sql
    odd_names.execute("use main")
    odd_names.execute(use_statement)
    assert _rows(odd_names, "select current_database()") == [(ODD_DATABASE,)]


def test_drop_removes_only_the_relation_it_was_asked_about(
    odd_names: DuckDbConnection,
) -> None:
    relation_items = _odd_relations(odd_names)
    # views first, since each one reads the table it is named after
    for label in sorted(relation_items, key=lambda label: not label.startswith("v ")):
        relation_item = relation_items[label]
        if label.startswith("v "):
            execute_drop_view_statement(relation_item, RecordingDriver())
        else:
            execute_drop_table_statement(relation_item, RecordingDriver())
        remaining = _odd_relations(odd_names)
        assert label not in remaining
        assert len(remaining) == len(relation_items) - 1
        relation_items = remaining

    schema_item = _odd_schema(odd_names)
    execute_drop_schema_statement(schema_item, RecordingDriver())
    assert schema_item.parent is not None
    assert [item.label for item in schema_item.parent.fetch_children()] == ["main"]


@pytest.mark.parametrize(
    "stem", ["it's", "semi;colon", "my file", "2024-sales", "select", "Mixed Case"]
)
def test_dot_open_attaches_and_uses_a_path_that_needs_quoting(
    tmp_path: Path, stem: str
) -> None:
    database_path = tmp_path / f"{stem}.db"
    script_path = tmp_path / "myscript"
    script_path.write_text(
        f'.open "{database_path}"\ncreate table opened as select 1;\n'
    )
    conn = DuckDbAdapter([":memory:"], init_path=script_path).connect()
    assert _rows(conn, "select current_database()") == [(stem,)]
    assert conn.conn.execute(
        "select database_name from duckdb_tables() where table_name = 'opened'"
    ).fetchall() == [(stem,)]


def test_dot_open_reads_a_bare_path_with_a_quote_in_it(tmp_path: Path) -> None:
    database_path = tmp_path / "it's.db"
    duckdb.connect(str(database_path)).close()
    script_path = tmp_path / "myscript"
    script_path.write_text(f".open --readonly {database_path}\n")
    conn = DuckDbAdapter([":memory:"], init_path=script_path).connect()
    assert conn.conn.execute(
        "select database_name, readonly from duckdb_databases() where path = ?",
        [str(database_path)],
    ).fetchall() == [("it's", True)]


def test_connect_attaches_a_path_that_needs_quoting(tmp_path: Path) -> None:
    other_path = tmp_path / "it's;x.db"
    conn = DuckDbAdapter(
        [str(tmp_path / "main.db"), str(other_path)], no_init=True
    ).connect()
    assert conn.conn.execute(
        "select database_name from duckdb_databases() where path = ?",
        [str(other_path)],
    ).fetchall() == [("it's;x",)]


def test_custom_extension_repo_is_set_verbatim() -> None:
    repository = "https://example.com/it's\\here"
    conn = DuckDbAdapter(
        [":memory:"], custom_extension_repo=repository, no_init=True
    ).connect()
    assert _rows(conn, "select current_setting('custom_extension_repository')") == [
        (repository,)
    ]


def test_validate_sql_reads_a_quote_as_part_of_the_text() -> None:
    conn = DuckDbAdapter([":memory:"], no_init=True).connect()
    assert conn.validate_sql("select 'it''s'") == "select 'it''s'"
    assert conn.validate_sql("select ') union selec 1 --'") == (
        "select ') union selec 1 --'"
    )
    assert conn.validate_sql("select 'it's'") == ""


def test_a_user_defined_name_completes_quoted_only_when_it_must_be() -> None:
    conn = DuckDbAdapter([":memory:"], no_init=True).connect()
    conn.execute('create macro "my macro"(a) as a')
    conn.execute('create macro "Select"(a) as a')
    # a column_name keyword, which is not reserved but cannot name a function
    conn.execute('create macro "between"(a) as a')
    # an unreserved keyword, which can
    conn.execute('create macro "action"(a) as a')
    conn.execute("create macro my_macro(a) as a")
    conn.execute("""create type "my type" as enum ('a')""")
    user_defined_labels = ("my macro", "Select", "between", "action", "my_macro")
    values = {
        completion.label: completion.value
        for completion in conn.get_completions()
        if completion.label in (*user_defined_labels, "my type", "sum")
        and completion.type_label != "kw"
    }
    assert values == {
        "my macro": '"my macro"',
        "Select": '"Select"',
        "between": '"between"',
        "action": "action",
        "my_macro": "my_macro",
        "my type": '"my type"',
        "sum": "sum",
    }
    for value in (values[label] for label in user_defined_labels):
        assert _rows(conn, f"select {value}(1)") == [(1,)]


@pytest.mark.parametrize(
    "script", ["select 1;;\ncreate table after as select 2;", "select 1;\n-- done\n"]
)
def test_an_init_script_with_a_statement_that_runs_nothing(
    tmp_path: Path, script: str
) -> None:
    """A lone separator or a trailing comment is a statement with no result."""
    script_path = tmp_path / "myscript"
    script_path.write_text(script)
    conn = DuckDbAdapter([":memory:"], init_path=script_path).connect()
    assert _rows(conn, "select 1") == [(1,)]
