from pathlib import Path
from typing import List, Sequence, Tuple, Union

import duckdb

from harlequin.exception import HarlequinExit

COLS = List[Tuple[str, str]]
TABLES = List[Tuple[str, str, COLS]]
SCHEMAS = List[Tuple[str, TABLES]]
Catalog = List[Tuple[str, SCHEMAS]]


def connect(
    db_path: Sequence[Union[str, Path]],
    read_only: bool = False,
    md_token: Union[str, None] = None,
    md_saas: bool = False,
) -> duckdb.DuckDBPyConnection:
    if not db_path:
        db_path = [":memory:"]
    primary_db, *other_dbs = db_path
    token = f"?token={md_token}" if md_token else ""
    saas = "?saas_mode=true" if md_saas else ""
    try:
        connection = duckdb.connect(
            database=f"{primary_db}{token}{saas}", read_only=read_only
        )
        for db in other_dbs:
            connection.execute(f"attach '{db}'{' (READ_ONLY)' if read_only else ''}")
    except (duckdb.CatalogException, duckdb.IOException) as e:
        from rich import print
        from rich.panel import Panel

        print(
            Panel.fit(
                str(e),
                title="DuckDB couldn't connect to your database.",
                title_align="left",
                border_style="red",
                subtitle="Try again?",
                subtitle_align="right",
            )
        )

        raise HarlequinExit() from None
    else:
        return connection


def get_databases(conn: duckdb.DuckDBPyConnection) -> List[Tuple[str]]:
    return conn.execute("pragma show_databases").fetchall()


def get_schemas(conn: duckdb.DuckDBPyConnection, database: str) -> List[Tuple[str]]:
    schemas = conn.execute(
        "select schema_name "
        "from information_schema.schemata "
        "where "
        "    catalog_name = ? "
        "    and schema_name not in ('pg_catalog', 'information_schema') "
        "order by 1",
        [database],
    ).fetchall()
    return schemas


def get_tables(
    conn: duckdb.DuckDBPyConnection, database: str, schema: str
) -> List[Tuple[str, str]]:
    tables = conn.execute(
        "select table_name, table_type "
        "from information_schema.tables "
        "where "
        "    table_catalog = ? "
        "    and table_schema = ? "
        "order by 1",
        [database, schema],
    ).fetchall()
    return tables


def get_columns(
    conn: duckdb.DuckDBPyConnection, database: str, schema: str, table: str
) -> List[Tuple[str, str]]:
    columns = conn.execute(
        "select column_name, data_type "
        "from information_schema.columns "
        "where "
        "    table_catalog = ? "
        "    and table_schema = ? "
        "    and table_name = ? "
        "order by 1",
        [database, schema, table],
    ).fetchall()
    return columns


def get_catalog(conn: duckdb.DuckDBPyConnection) -> Catalog:
    data: Catalog = []
    databases = get_databases(conn)
    for (database,) in databases:
        schemas = get_schemas(conn, database)
        schemas_data: SCHEMAS = []
        for (schema,) in schemas:
            tables = get_tables(conn, database, schema)
            tables_data: TABLES = []
            for table, kind in tables:
                columns = get_columns(conn, database, schema, table)
                tables_data.append((table, kind, columns))
            schemas_data.append((schema, tables_data))
        data.append((database, schemas_data))
    return data
