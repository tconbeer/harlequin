from pathlib import Path
from typing import List, Sequence, Tuple, Union

import duckdb
from duckdb.typing import DuckDBPyType

from harlequin.exception import HarlequinConnectionError
from harlequin.export_options import (
    CSVOptions,
    ExportOptions,
    JSONOptions,
    ParquetOptions,
)

COLS = List[Tuple[str, str]]
TABLES = List[Tuple[str, str, COLS]]
SCHEMAS = List[Tuple[str, TABLES]]
Catalog = List[Tuple[str, SCHEMAS]]


RELATION_TYPE_MAPPING = {
    "BASE TABLE": "t",
    "LOCAL TEMPORARY": "tmp",
    "VIEW": "v",
}

COLUMN_TYPE_MAPPING = {
    "SQLNULL": "\\n",
    "BOOLEAN": "t/f",
    "TINYINT": "#",
    "UTINYINT": "u#",
    "SMALLINT": "#",
    "USMALLINT": "u#",
    "INTEGER": "#",
    "UINTEGER": "u#",
    "BIGINT": "##",
    "UBIGINT": "u##",
    "HUGEINT": "###",
    "UUID": "uid",
    "FLOAT": "#.#",
    "DOUBLE": "#.#",
    "DATE": "d",
    "TIMESTAMP": "ts",
    "TIMESTAMP_MS": "ts",
    "TIMESTAMP_NS": "ts",
    "TIMESTAMP_S": "ts",
    "TIME": "t",
    "TIME_TZ": "ttz",
    "TIMESTAMP_TZ": "ttz",
    "TIMESTAMP WITH TIME ZONE": "ttz",
    "VARCHAR": "s",
    "BLOB": "0b",
    "BIT": "010",
    "INTERVAL": "|-|",
    # these types don't have python classes
    "DECIMAL": "#.#",
    "REAL": "#.#",
    "LIST": "[]",
    "STRUCT": "{}",
    "MAP": "{}",
}

UNKNOWN_TYPE = "?"


def _split_script(script: str) -> List[str]:
    """
    DuckDB init scripts can contain SQL queries or dot commands. The SQL
    queries may contain newlines, but the dot commands are newline-terminated.
    This takes a raw script and returns a list of executable commands
    """
    lines = script.splitlines()
    commands: List[str] = []
    i = 0
    for j, line in enumerate(lines):
        if line.startswith("."):
            commands.append("\n".join(lines[i:j]))
            commands.append(line)
            i = j + 1
    commands.append("\n".join(lines[i:]))
    return [command.strip() for command in commands if command]


def _rewrite_dot_open(command: str) -> str:
    """
    Rewrites .open command into its SQL equivalent.
    """
    args = command.split()[1:]
    if not args:
        return "attach ':memory:'; use memory;"
    else:
        # --readonly is only supported option
        if len(args) == 2 and args[0] == "--readonly":
            option = " (READ_ONLY)"
            db_path = Path(args[1])
        else:
            option = ""
            db_path = Path(args[0])

        return f"attach '{db_path}'{option} as {db_path.stem}; use {db_path.stem};"


def _rewrite_init_command(command: str) -> str:
    """
    DuckDB init scripts can contain dot commands, which can only be executed
    by the CLI, not the python API. Here, we rewrite some common ones into
    SQL, and rewrite the others to be no-ops.
    """
    if not command.startswith("."):
        return command
    elif command.startswith(".open"):
        return _rewrite_dot_open(command)
    else:
        return ""


def connect(
    db_path: Sequence[Union[str, Path]],
    init_script: Tuple[Path, str] = (Path(), ""),
    read_only: bool = False,
    allow_unsigned_extensions: bool = False,
    extensions: Union[List[str], None] = None,
    force_install_extensions: bool = False,
    custom_extension_repo: Union[str, None] = None,
    md_token: Union[str, None] = None,
    md_saas: bool = False,
) -> duckdb.DuckDBPyConnection:
    if not db_path:
        db_path = [":memory:"]
    primary_db, *other_dbs = db_path
    token = f"?token={md_token}" if md_token else ""
    saas = "?saas_mode=true" if md_saas else ""
    config = {"allow_unsigned_extensions": str(allow_unsigned_extensions).lower()}

    try:
        connection = duckdb.connect(
            database=f"{primary_db}{token}{saas}", read_only=read_only, config=config
        )
        for db in other_dbs:
            connection.execute(f"attach '{db}'{' (READ_ONLY)' if read_only else ''}")
    except (duckdb.CatalogException, duckdb.IOException) as e:
        raise HarlequinConnectionError(
            str(e), title="DuckDB couldn't connect to your database."
        ) from e

    if custom_extension_repo:
        connection.execute(
            f"SET custom_extension_repository='{custom_extension_repo}';"
        )

    if extensions:
        try:
            for extension in extensions:
                # todo: support installing from a URL instead.
                connection.install_extension(
                    extension=extension, force_install=force_install_extensions
                )
                connection.load_extension(extension=extension)
        except (duckdb.HTTPException, duckdb.IOException) as e:
            raise HarlequinConnectionError(
                str(e), title="DuckDB couldn't install or load your extension."
            ) from e

    if init_script:
        try:
            for command in _split_script(init_script[1]):
                rewritten_command = _rewrite_init_command(command)
                for cmd in rewritten_command.split(";"):
                    connection.execute(cmd)
        except duckdb.Error as e:
            msg = f"Attempted to execute script at {init_script[0]}\n{e}"
            raise HarlequinConnectionError(
                msg, title="DuckDB could not execute your initialization script."
            ) from e

    return connection


def export_relation(
    relation: duckdb.DuckDBPyRelation,
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    options: ExportOptions,
) -> None:
    final_path = str(path.expanduser())
    if isinstance(options, CSVOptions):
        relation.write_csv(
            file_name=final_path,
            sep=options.sep,
            na_rep=options.nullstr,
            header=options.header,
            quotechar=options.quote,
            escapechar=options.escape,
            date_format=options.dateformat if options.dateformat else None,
            timestamp_format=options.timestampformat
            if options.timestampformat
            else None,
            quoting="ALL" if options.force_quote else None,
            compression=options.compression,
            encoding=options.encoding,
        )
    elif isinstance(options, ParquetOptions):
        relation.write_parquet(file_name=final_path, compression=options.compression)
    elif isinstance(options, JSONOptions):
        compression = (
            f", COMPRESSION {options.compression}"
            if options.compression in ("gzip", "zstd", "uncompressed")
            else ""
        )
        print("compression: ", compression)
        date_format = f", DATEFORMAT {options.dateformat}" if options.dateformat else ""
        ts_format = (
            f", TIMESTAMPFORMAT {options.timestampformat}"
            if options.timestampformat
            else ""
        )
        connection.sql(
            f"copy ({relation.sql_query()}) to '{final_path}' "
            "(FORMAT JSON"
            f"{', ARRAY TRUE' if options.array else ''}"
            f"{compression}{date_format}{ts_format}"
            ")"
        )


def get_catalog(conn: duckdb.DuckDBPyConnection) -> Catalog:
    data: Catalog = []
    databases = _get_databases(conn)
    for (database,) in databases:
        schemas = _get_schemas(conn, database)
        schemas_data: SCHEMAS = []
        for (schema,) in schemas:
            tables = _get_tables(conn, database, schema)
            tables_data: TABLES = []
            for table, kind in tables:
                columns = _get_columns(conn, database, schema, table)
                tables_data.append((table, kind, columns))
            schemas_data.append((schema, tables_data))
        data.append((database, schemas_data))
    return data


def get_relation_label(
    rel_name: str, rel_type: str, type_color: str = "#888888"
) -> str:
    short_type = _short_relation_type(rel_type)
    return f"{rel_name} [{type_color}]{short_type}[/]"


def get_column_label(
    col_name: str,
    col_type: Union[DuckDBPyType, str],
    type_color: str = "#888888",
) -> str:
    short_type = _short_column_type(col_type)
    return f"{col_name} [{type_color}]{short_type}[/]"


def get_column_labels_for_relation(
    relation: duckdb.DuckDBPyRelation, type_color: str = "#888888"
) -> List[str]:
    return [
        get_column_label(col_name=col_name, col_type=col_type, type_color=type_color)
        for col_name, col_type in zip(relation.columns, relation.dtypes)
    ]


def _get_databases(conn: duckdb.DuckDBPyConnection) -> List[Tuple[str]]:
    return conn.execute("pragma show_databases").fetchall()


def _get_schemas(conn: duckdb.DuckDBPyConnection, database: str) -> List[Tuple[str]]:
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


def _get_tables(
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


def _get_columns(
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


def _short_relation_type(native_type: str) -> str:
    return RELATION_TYPE_MAPPING.get(native_type, UNKNOWN_TYPE)


def _short_column_type(native_type: Union[DuckDBPyType, str]) -> str:
    """
    In duckdb v0.8.0, relation.dtypes started returning a DuckDBPyType,
    instead of a string. However, this type isn't an ENUM, and there
    aren't classes for all types, so it's hard
    to check class members. So we just convert to a string and split
    complex types on their first paren to match our dictionary.
    """
    return COLUMN_TYPE_MAPPING.get(str(native_type).split("(")[0], UNKNOWN_TYPE)
