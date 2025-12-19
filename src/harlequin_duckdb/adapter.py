from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import duckdb
from duckdb.typing import DuckDBPyType
from textual_fastdatatable.backend import AutoBackendType

from harlequin.adapter import HarlequinAdapter, HarlequinConnection, HarlequinCursor
from harlequin.autocomplete.completion import HarlequinCompletion
from harlequin.catalog import Catalog, CatalogItem
from harlequin.exception import (
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinQueryError,
)
from harlequin_duckdb.catalog import DatabaseCatalogItem
from harlequin_duckdb.cli_options import DUCKDB_OPTIONS
from harlequin_duckdb.completions import get_completion_data

IN_MEMORY_CONN_STR = (":memory:",)


class DuckDbCursor(HarlequinCursor):
    def __init__(
        self, conn: DuckDbConnection, relation: duckdb.DuckDBPyRelation
    ) -> None:
        self.conn = conn
        self.relation = relation

    def columns(self) -> list[tuple[str, str]]:
        return list(
            zip(
                self.relation.columns,
                map(self.conn._short_column_type, self.relation.dtypes),
                strict=False,
            )
        )

    def set_limit(self, limit: int) -> HarlequinCursor:
        try:
            self.relation = self.relation.limit(limit)
        except duckdb.Error:
            pass
        return self

    def fetchall(self) -> AutoBackendType | None:
        try:
            result = self.relation.fetch_arrow_table()
        except duckdb.InterruptException:
            return None
        except duckdb.Error as e:
            raise HarlequinQueryError(
                msg=str(e), title="DuckDB raised an error when running your query:"
            ) from e
        return result

    def fetchone(self) -> tuple | None:
        try:
            result = self.relation.fetchone()
        except duckdb.InterruptException:
            return None
        except duckdb.Error as e:
            raise HarlequinQueryError(
                msg=str(e), title="DuckDB raised an error when running your query:"
            ) from e
        return result


class DuckDbConnection(HarlequinConnection):
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
        "STRUCT": "{}",
        "MAP": "{m}",
    }

    UNKNOWN_TYPE = "?"

    def __init__(self, conn: duckdb.DuckDBPyConnection, init_message: str = "") -> None:
        self.conn: duckdb.DuckDBPyConnection = conn
        self.init_message = init_message

    def execute(self, query: str) -> DuckDbCursor | None:
        try:
            rel = self.conn.sql(query)
        except duckdb.InterruptException:
            return None
        except duckdb.Error as e:
            raise HarlequinQueryError(
                msg=str(e),
                title="DuckDB raised an error when compiling or running your query:",
            ) from e

        if rel is not None:
            return DuckDbCursor(conn=self, relation=rel)
        else:
            return None

    def cancel(self) -> None:
        self.conn.interrupt()

    def get_catalog(self) -> Catalog:
        catalog_items: list[CatalogItem] = []
        databases = self._get_databases()
        for (database_label,) in databases:
            catalog_items.append(
                DatabaseCatalogItem.from_label(label=database_label, connection=self)
            )
        return Catalog(items=catalog_items)

    def get_completions(self) -> list[HarlequinCompletion]:
        cur = self.conn.cursor()
        return [
            HarlequinCompletion(
                label=label,
                type_label=type_label,
                value=label,
                priority=priority,
                context=context,
            )
            for label, type_label, priority, context in get_completion_data(cur)
        ]

    def validate_sql(self, text: str) -> str:
        cur = self.conn.cursor()
        escaped = text.replace("'", "''")
        try:
            (parsed,) = cur.sql(  # type: ignore
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
        cur = self.conn.cursor()
        return cur.execute("pragma show_databases").fetchall()

    def _get_schemas(self, database: str) -> list[tuple[str]]:
        cur = self.conn.cursor()
        schemas = cur.execute(
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
        cur = self.conn.cursor()
        tables = cur.execute(
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
        cur = self.conn.cursor()
        columns = cur.execute(
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
        base_type = str(native_type).split("(")[0].split("[")[0]
        short_base_type = cls.COLUMN_TYPE_MAPPING.get(base_type, cls.UNKNOWN_TYPE)
        if str(native_type).endswith("[]"):
            return f"[{short_base_type}]"
        else:
            return short_base_type


class DuckDbAdapter(HarlequinAdapter):
    ADAPTER_OPTIONS = DUCKDB_OPTIONS
    COPY_FORMATS = None
    IMPLEMENTS_CANCEL = True
    ADAPTER_DETAILS = "This is a DuckDB adapter part of Harlequin core."

    def __init__(
        self,
        conn_str: Sequence[str],
        init_path: Path | str | None = None,
        no_init: bool | str = False,
        read_only: bool | str = False,
        allow_unsigned_extensions: bool | str = False,
        extension: list[str] | None = None,
        force_install_extensions: bool = False,
        custom_extension_repo: str | None = None,
        md_token: str | None = None,
        md_saas: bool = False,
        **_: Any,
    ) -> None:
        try:
            self.conn_str = (
                conn_str if conn_str and conn_str != ("",) else IN_MEMORY_CONN_STR
            )
            self.init_path = (
                Path(init_path).expanduser().resolve()
                if init_path is not None
                else Path.home() / ".duckdbrc"
            )
            self.no_init = bool(no_init)
            self.read_only = bool(read_only)
            self.allow_unsigned_extensions = bool(allow_unsigned_extensions)
            self.extensions = extension if extension is not None else []
            self.force_install_extensions = force_install_extensions
            self.custom_extension_repo = custom_extension_repo
            self.md_token = md_token
            self.md_saas = md_saas
        except (ValueError, TypeError) as e:
            raise HarlequinConfigError(
                msg=f"DuckDB adapter received bad config value: {e}",
                title="Harlequin could not initialize the selected adapter.",
            ) from e

    @property
    def connection_id(self) -> str | None:
        if self.conn_str == IN_MEMORY_CONN_STR:
            return ""
        return ",".join(
            [Path(conn).resolve().as_posix() for conn in sorted(self.conn_str)]
        )

    def connect(self) -> DuckDbConnection:
        primary_db, *other_dbs = self.conn_str
        token = f"?token={self.md_token}" if self.md_token else ""
        saas = "?saas_mode=true" if self.md_saas else ""
        config: dict[str, str | bool | int | float | list[str]] | None = {
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
            if "sqlite_scanner" in (msg := str(e)):
                msg = (
                    "DuckDB raised the following error when trying to open "
                    f"one or more database files:\n---\n{msg}\n---\n\n"
                    "Did you mean to use Harlequin's sqlite adapter instead? "
                    f"Maybe try:\nharlequin -a sqlite {' '.join(self.conn_str)}"
                )
            raise HarlequinConnectionError(
                msg, title="DuckDB couldn't connect to your database."
            ) from e

        if self.custom_extension_repo:
            connection.execute(
                f"SET custom_extension_repository='{self.custom_extension_repo}';"
            )

        for extension in self.extensions:
            try:
                # todo: support installing from a URL instead.
                connection.install_extension(
                    extension=extension, force_install=self.force_install_extensions
                )
                connection.load_extension(extension=extension)
            except (duckdb.HTTPException, duckdb.IOException) as e:
                raise HarlequinConnectionError(
                    str(e), title="DuckDB couldn't install or load your extension."
                ) from e

        msg = ""
        if self.init_path is not None and not self.no_init:
            init_script = self._read_init_script(self.init_path)
            try:
                count = 0
                for command in self._split_script(init_script):
                    rewritten_command = self._rewrite_init_command(command)
                    for cmd in rewritten_command.split(";"):
                        if cmd.strip():
                            connection.execute(cmd)
                            count += 1
            except duckdb.Error as e:
                msg = f"Attempted to execute script at {self.init_path}\n{e}"
                raise HarlequinConnectionError(
                    msg, title="DuckDB could not execute your initialization script."
                ) from e
            else:
                if count > 0:
                    msg = (
                        f"Executed {count} {'command' if count == 1 else 'commands'} "
                        f"from {self.init_path}"
                    )

        self.ADAPTER_DRIVER_DETAILS = f"""
Connected to database `{primary_db}`
        """
        return DuckDbConnection(conn=connection, init_message=msg)

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
