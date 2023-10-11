from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Final, Sequence

import duckdb
from duckdb.typing import DuckDBPyType
from textual_fastdatatable.backend import AutoBackendType

from harlequin.catalog import Catalog, CatalogItem
from harlequin.exception import (
    HarlequinConnectionError,
    HarlequinCopyError,
    HarlequinQueryError,
)
from harlequin.export_options import (
    CSVOptions,
    ExportOptions,
    JSONOptions,
    ParquetOptions,
)
from harlequin.options import HarlequinAdapterOption, HarlequinCopyOptions


class HarlequinCursor(ABC):
    @abstractmethod
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @abstractmethod
    def columns(self) -> list[tuple[str, str]]:
        """
        Gets a list of columns for the result set of the cursor.

        Returns: list[tuple[str, str]], where each tuple is the (name, type) of
            a column, where type should be a short (1-3 character) string. The
            columns must be ordered in the same order as the data returned by
            fetchall()
        """
        pass

    @abstractmethod
    def set_limit(self, limit: int) -> "HarlequinCursor":
        """
        Limits the number of results for future calls to fetchall().

        Args:
            limit (int): The maximum number of records to be returned
            by future calls to fetchall().

        Returns: HarlequinCursor, either a reference to self or a new
            cursor with the limit applied.
        """
        pass

    @abstractmethod
    def fetchall(self) -> AutoBackendType:
        """
        Returns data from the cursor's result set. Can return any type supported
        by textual-fastdatatable.

        Returns:
            pyarrow.Table |
            pyarrow.Record Batch |
            Sequence[Iterable[Any]] |
            Mapping[str, Sequence[Any]]
        """
        pass


class HarlequinConnection(ABC):
    @abstractmethod
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @abstractmethod
    def execute(self, query: str) -> HarlequinCursor | None:
        """
        Executes query and returns a cursor (for a select stmt) or None. Raises
        HarlequinQueryError if the database raises an error in response to the query.

        Args:
            query (str): The text of a single query to execute

        Returns: HarlequinCursor | None

        Raises: HarlequinQueryError
        """
        pass

    @abstractmethod
    def get_catalog(self) -> Catalog:
        """
        Introspects the connected database and returns a Catalog object with
        items for each database, schema, table, view, column, etc.

        Returns: Catalog
        """
        pass

    def copy(self, query: str, path: Path, options: ExportOptions) -> None:
        """
        Exports data returned by query to a file or directory at path, using
        options.
        Args:
            query (str): The text of the query (select stmt) to be executed.
            path (Path): The destination location for the file(s) to be written.
            options (HarlequinCopyOptions): An instance of copy options specific to
                this operation and adapter.

        Returns: None

        Raises:
            NotImplementedError if the adapter does not have copy functionality.
            HarlequinCopyError for all other exceptions during export.
        """
        raise NotImplementedError

    def validate_sql(self, text: str) -> str:
        """
        Parses text as one or more queries; returns text if parsing does not result
        in an error; otherwise returns the empty string ("").
        """
        raise NotImplementedError


class HarlequinAdapter(ABC):
    ADAPTER_OPTIONS: Final[list[HarlequinAdapterOption] | None] = None
    COPY_OPTIONS: Final[HarlequinCopyOptions | None] = None

    @abstractmethod
    def __init__(self, conn_str: Sequence[str], **options: Any) -> None:
        """
        A HarlequinAdapter is the main abstraction for a database backend for
        Harlequin. It must declare its configuration setting the ADAPTER_OPTIONS
        class variable. If the adapter supports copying (exporting
        data to a file or directory), it also must declare COPY_OPTIONS.
        """
        pass

    @abstractmethod
    def connect(self) -> HarlequinConnection:
        """
        Creates and returns an initialized connection to a database. Necessary config
        should be stored in the HarlequinAdapter instance when it is created.

        Returns: HarlequinConnection
        """
        pass

    @property
    def implements_copy(self) -> bool:
        return self.COPY_OPTIONS is not None


class HarlequinDuckDBCursor(HarlequinCursor):
    def __init__(
        self, conn: HarlequinDuckDBConnection, relation: duckdb.DuckDBPyRelation
    ) -> None:
        self.conn = conn
        self.relation = relation

    def columns(self) -> list[tuple[str, str]]:
        return list(
            zip(
                self.relation.columns,
                map(self.conn._short_column_type, self.relation.dtypes),
            )
        )

    def set_limit(self, limit: int) -> HarlequinCursor:
        try:
            self.relation = self.relation.limit(limit)
        except duckdb.Error:
            pass
        return self

    def fetchall(self) -> AutoBackendType:
        try:
            result = self.relation.fetch_arrow_table()
        except duckdb.Error as e:
            raise HarlequinQueryError(
                msg=str(e), title="DuckDB raised an error when running your query:"
            ) from e
        return result  # type: ignore


class HarlequinDuckDBConnection(HarlequinConnection):
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

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn: duckdb.DuckDBPyConnection = conn

    def copy(self, query: str, path: Path, options: ExportOptions) -> None:
        if not query:
            raise HarlequinCopyError("Cannot export result of empty query.")
        try:
            cursor = self.execute(query)
        except HarlequinQueryError as e:
            raise HarlequinCopyError(msg=e.msg, title=e.title) from e
        if cursor is None:
            raise HarlequinCopyError("Cannot export result of a DDL/DML query.")
        final_path = str(path.expanduser())
        if isinstance(options, CSVOptions):
            try:
                cursor.relation.write_csv(
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
            except (duckdb.Error, OSError) as e:
                raise HarlequinCopyError(
                    str(e),
                    title=(
                        "DuckDB raised an error when writing your query "
                        "to a CSV file."
                    ),
                ) from e
        elif isinstance(options, ParquetOptions):
            try:
                cursor.relation.write_parquet(
                    file_name=final_path, compression=options.compression
                )
            except (duckdb.Error, OSError) as e:
                raise HarlequinCopyError(
                    str(e),
                    title=(
                        "DuckDB raised an error when writing your query "
                        "to a Parquet file."
                    ),
                ) from e
        elif isinstance(options, JSONOptions):
            compression = (
                f", COMPRESSION {options.compression}"
                if options.compression in ("gzip", "zstd", "uncompressed")
                else ""
            )
            print("compression: ", compression)
            date_format = (
                f", DATEFORMAT {options.dateformat}" if options.dateformat else ""
            )
            ts_format = (
                f", TIMESTAMPFORMAT {options.timestampformat}"
                if options.timestampformat
                else ""
            )
            try:
                self.execute(
                    f"copy ({query}) to '{final_path}' "
                    "(FORMAT JSON"
                    f"{', ARRAY TRUE' if options.array else ''}"
                    f"{compression}{date_format}{ts_format}"
                    ")"
                )
            except (HarlequinQueryError, OSError) as e:
                raise HarlequinCopyError(
                    str(e),
                    title=(
                        "DuckDB raised an error when writing your query "
                        "to a JSON file."
                    ),
                ) from e

    def execute(self, query: str) -> HarlequinDuckDBCursor | None:
        try:
            rel = self.conn.sql(query)
        except duckdb.Error as e:
            raise HarlequinQueryError(
                msg=str(e),
                title="DuckDB raised an error when compiling or running your query:",
            ) from e

        if rel is not None:
            return HarlequinDuckDBCursor(conn=self, relation=rel)
        else:
            return None

    def get_catalog(self) -> Catalog:
        catalog_items: list[CatalogItem] = []
        databases = self._get_databases()
        for (database,) in databases:
            database_identifier = f'"{database}"'
            schemas = self._get_schemas(database)
            schema_items: list[CatalogItem] = []
            for (schema,) in schemas:
                schema_identifier = f'{database_identifier}."{schema}"'
                tables = self._get_tables(database, schema)
                table_items: list[CatalogItem] = []
                for table, kind in tables:
                    table_identifier = f'{schema_identifier}."{table}"'
                    columns = self._get_columns(database, schema, table)
                    column_items = [
                        CatalogItem(
                            qualified_identifier=f'{table_identifier}."{col[0]}"',
                            query_name=f'"{col[0]}"',
                            label=col[0],
                            type_label=self._short_column_type(col[1]),
                        )
                        for col in columns
                    ]
                    table_items.append(
                        CatalogItem(
                            qualified_identifier=table_identifier,
                            query_name=table_identifier,
                            label=table,
                            type_label=self._short_relation_type(kind),
                            children=column_items,
                        )
                    )
                schema_items.append(
                    CatalogItem(
                        qualified_identifier=schema_identifier,
                        query_name=schema_identifier,
                        label=schema,
                        type_label="sch",
                        children=table_items,
                    )
                )
            catalog_items.append(
                CatalogItem(
                    qualified_identifier=database_identifier,
                    query_name=database_identifier,
                    label=database,
                    type_label="db",
                    children=schema_items,
                )
            )
        return Catalog(items=catalog_items)

    def validate_sql(self, text: str) -> str:
        """
        Parses text as one or more queries; returns text if parsing does not result
        in an error; otherwise returns the empty string ("").

        Args:
            text (str): The text to be validated

        Returns: str, either the original text or the empty string.
        """
        escaped = text.replace("'", "''")
        try:
            (parsed,) = self.conn.sql(  # type: ignore
                f"select json_serialize_sql('{escaped}')"
            ).fetchone()
        except HarlequinQueryError:
            return ""
        result = json.loads(parsed)
        # DDL statements return an error of type "not implemented"
        if result.get("error", True) and result.get("error_type", "") == "parser":
            return ""
        else:
            return text

    def _get_databases(self) -> list[tuple[str]]:
        return self.conn.execute("pragma show_databases").fetchall()

    def _get_schemas(self, database: str) -> list[tuple[str]]:
        schemas = self.conn.execute(
            "select schema_name "
            "from information_schema.schemata "
            "where "
            "    catalog_name = ? "
            "    and schema_name not in ('pg_catalog', 'information_schema') "
            "order by 1",
            [database],
        ).fetchall()
        return schemas

    def _get_tables(self, database: str, schema: str) -> list[tuple[str, str]]:
        tables = self.conn.execute(
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
        self, database: str, schema: str, table: str
    ) -> list[tuple[str, str]]:
        columns = self.conn.execute(
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

    @classmethod
    def _short_relation_type(cls, native_type: str) -> str:
        return cls.RELATION_TYPE_MAPPING.get(native_type, cls.UNKNOWN_TYPE)

    @classmethod
    def _short_column_type(cls, native_type: DuckDBPyType | str) -> str:
        """
        In duckdb v0.8.0, relation.dtypes started returning a DuckDBPyType,
        instead of a string. However, this type isn't an ENUM, and there
        aren't classes for all types, so it's hard
        to check class members. So we just convert to a string and split
        complex types on their first paren to match our dictionary.
        """
        return cls.COLUMN_TYPE_MAPPING.get(
            str(native_type).split("(")[0], cls.UNKNOWN_TYPE
        )


class DuckDBAdapter(HarlequinAdapter):
    def __init__(
        self,
        conn_str: Sequence[str],
        init_path: Path | None = None,
        no_init: bool = False,
        read_only: bool = False,
        allow_unsigned_extensions: bool = False,
        extension: list[str] | None = None,
        force_install_extensions: bool = False,
        custom_extension_repo: str | None = None,
        md_token: str | None = None,
        md_saas: bool = False,
    ) -> None:
        self.conn_str = conn_str if conn_str else (":memory:",)
        self.init_path = init_path
        self.no_init = no_init
        self.read_only = read_only
        self.allow_unsigned_extensions = allow_unsigned_extensions
        self.extensions = extension if extension is not None else []
        self.force_install_extensions = force_install_extensions
        self.custom_extension_repo = custom_extension_repo
        self.md_token = md_token
        self.md_saas = md_saas

    def connect(self) -> HarlequinDuckDBConnection:
        primary_db, *other_dbs = self.conn_str
        token = f"?token={self.md_token}" if self.md_token else ""
        saas = "?saas_mode=true" if self.md_saas else ""
        config = {
            "allow_unsigned_extensions": str(self.allow_unsigned_extensions).lower()
        }

        try:
            connection = duckdb.connect(
                database=f"{primary_db}{token}{saas}",
                read_only=self.read_only,
                config=config,
            )
            for db in other_dbs:
                connection.execute(
                    f"attach '{db}'{' (READ_ONLY)' if self.read_only else ''}"
                )
        except (duckdb.CatalogException, duckdb.IOException) as e:
            raise HarlequinConnectionError(
                str(e), title="DuckDB couldn't connect to your database."
            ) from e

        if self.custom_extension_repo:
            connection.execute(
                f"SET custom_extension_repository='{self.custom_extension_repo}';"
            )

        if self.extensions:
            try:
                for extension in self.extensions:
                    # todo: support installing from a URL instead.
                    connection.install_extension(
                        extension=extension, force_install=self.force_install_extensions
                    )
                    connection.load_extension(extension=extension)
            except (duckdb.HTTPException, duckdb.IOException) as e:
                raise HarlequinConnectionError(
                    str(e), title="DuckDB couldn't install or load your extension."
                ) from e

        if self.init_path is not None and not self.no_init:
            init_script = self._read_init_script(self.init_path)
            try:
                for command in self._split_script(init_script):
                    rewritten_command = self._rewrite_init_command(command)
                    for cmd in rewritten_command.split(";"):
                        connection.execute(cmd)
            except duckdb.Error as e:
                msg = f"Attempted to execute script at {init_script[0]}\n{e}"
                raise HarlequinConnectionError(
                    msg, title="DuckDB could not execute your initialization script."
                ) from e

        return HarlequinDuckDBConnection(conn=connection)

    @staticmethod
    def _read_init_script(init_path: Path) -> str:
        try:
            with open(init_path.expanduser(), "r") as f:
                init_script = f.read()
        except OSError:
            init_script = ""
        return init_script

    @staticmethod
    def _split_script(script: str) -> list[str]:
        """
        DuckDB init scripts can contain SQL queries or dot commands. The SQL
        queries may contain newlines, but the dot commands are newline-terminated.
        This takes a raw script and returns a list of executable commands
        """
        lines = script.splitlines()
        commands: list[str] = []
        i = 0
        for j, line in enumerate(lines):
            if line.startswith("."):
                commands.append("\n".join(lines[i:j]))
                commands.append(line)
                i = j + 1
        commands.append("\n".join(lines[i:]))
        return [command.strip() for command in commands if command]

    @staticmethod
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

    @classmethod
    def _rewrite_init_command(cls, command: str) -> str:
        """
        DuckDB init scripts can contain dot commands, which can only be executed
        by the CLI, not the python API. Here, we rewrite some common ones into
        SQL, and rewrite the others to be no-ops.
        """
        if not command.startswith("."):
            return command
        elif command.startswith(".open"):
            return cls._rewrite_dot_open(command)
        else:
            return ""
