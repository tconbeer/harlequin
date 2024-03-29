from __future__ import annotations

import sqlite3
from itertools import zip_longest
from pathlib import Path
from typing import Any, Literal, Sequence
from urllib.parse import unquote, urlparse

from harlequin.adapter import HarlequinAdapter, HarlequinConnection, HarlequinCursor
from harlequin.autocomplete.completion import HarlequinCompletion
from harlequin.catalog import Catalog, CatalogItem
from harlequin.exception import (
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinQueryError,
)
from harlequin.options import HarlequinAdapterOption, HarlequinCopyFormat
from textual_fastdatatable.backend import AutoBackendType

from harlequin_sqlite.cli_options import SQLITE_OPTIONS
from harlequin_sqlite.completions import get_completion_data


class HarlequinSqliteCursor(HarlequinCursor):
    def __init__(self, conn: HarlequinSqliteConnection, cur: sqlite3.Cursor) -> None:
        self.conn = conn
        self.cur = cur
        self._limit: int | None = None
        _first_row = cur.fetchone()
        self.has_records = _first_row is not None
        self._first_row: tuple[Any, ...] = _first_row or tuple(
            [None] * len(cur.description)
        )

    def columns(self) -> list[tuple[str, str]]:
        col_names = [col[0] for col in self.cur.description]
        col_types = [
            self.conn._short_column_type_from_python_object(value)
            for value in self._first_row
        ]
        return list(zip_longest(col_names, col_types, fillvalue="?"))

    def set_limit(self, limit: int) -> "HarlequinSqliteCursor":
        self._limit = limit
        return self

    def fetchall(self) -> AutoBackendType | None:
        if self.has_records:
            remaining_rows = (
                self.cur.fetchall()
                if self._limit is None
                else self.cur.fetchmany(self._limit - 1)
            )
            return [self._first_row, *remaining_rows]
        else:
            return None


class HarlequinSqliteConnection(HarlequinConnection):
    def __init__(self, conn: sqlite3.Connection, init_message: str = "") -> None:
        self.conn = conn
        self.init_message = init_message

    def execute(self, query: str) -> HarlequinSqliteCursor | None:
        try:
            cur = self.conn.execute(query)
        except sqlite3.Error as e:
            raise HarlequinQueryError(
                msg=str(e),
                title="SQLite raised an error when compiling or running your query:",
            ) from e

        if cur.description is not None:
            return HarlequinSqliteCursor(conn=self, cur=cur)
        else:
            return None

    def get_catalog(self) -> Catalog:
        catalog_items: list[CatalogItem] = []
        databases = self._get_databases()
        for db_name in databases:
            relations = self._get_relations(db_name=db_name)
            db_identifier = f'"{db_name}"'
            rel_items: list[CatalogItem] = []
            for rel_name, rel_type in relations:
                rel_identifier = f'{db_identifier}."{rel_name}"'
                cols = self._get_columns(db_name=db_name, rel_name=rel_name)
                col_items = [
                    CatalogItem(
                        qualified_identifier=f'{rel_identifier}."{col_name}"',
                        query_name=f'"{col_name}"',
                        label=col_name,
                        type_label=self._short_column_type(col_type),
                    )
                    for _, col_name, col_type, *_ in cols
                ]
                rel_items.append(
                    CatalogItem(
                        qualified_identifier=rel_identifier,
                        query_name=rel_identifier,
                        label=rel_name,
                        type_label=self._short_relation_type(rel_type),
                        children=col_items,
                    )
                )
            catalog_items.append(
                CatalogItem(
                    qualified_identifier=db_identifier,
                    query_name=db_identifier,
                    label=db_name,
                    type_label="db",
                    children=rel_items,
                )
            )
        return Catalog(items=catalog_items)

    def get_completions(self) -> list[HarlequinCompletion]:
        return get_completion_data(self.conn)

    def _get_databases(self) -> list[str]:
        objects: list[tuple[str, str, str]] = self.conn.execute(
            "pragma database_list"
        ).fetchall()
        return [db_name for _, db_name, _ in objects]

    def _get_relations(self, db_name: str) -> list[tuple[str, str]]:
        objects = self.conn.execute(
            f'select type, name from "{db_name}".sqlite_schema'
        ).fetchall()
        relations = [(name, typ) for typ, name in objects if typ in ("table", "view")]
        return relations

    def _get_columns(self, db_name: str, rel_name: str) -> list[tuple[str, str, str]]:
        return self.conn.execute(
            f"pragma {db_name}.table_info('{rel_name}')"
        ).fetchall()

    @staticmethod
    def _short_column_type(raw_type: str) -> str:
        # first get the storage affinity for a type decl
        if raw_type is None or raw_type == "":
            affinity = ""

        typ = raw_type.lower()
        if "int" in typ:
            affinity = "INTEGER"
        elif any([x in typ for x in ["char", "clob", "text"]]):
            affinity = "TEXT"
        elif "blob" in typ:
            affinity = "BLOB"
        elif any([x in typ for x in ["real", "floa", "doub"]]):
            affinity = "REAL"
        else:
            affinity = "NUMERIC"

        # then lookup the abbreviation for the affinity
        mapping = {
            "": "",
            "TEXT": "s",
            "NUMERIC": "#.#",
            "INTEGER": "##",
            "REAL": "#.#",
            "BLOB": "b",
        }
        return mapping.get(affinity, "?")

    @staticmethod
    def _short_column_type_from_python_object(obj: object) -> str:
        mapping: dict[type | None, str] = {
            None: "",
            str: "s",
            float: "#.#",
            int: "##",
            object: "b",
        }
        return mapping.get(type(obj), "?")

    @staticmethod
    def _short_relation_type(raw_type: str) -> str:
        mapping = {"table": "t", "view": "v"}
        return mapping.get(raw_type, "?")

    def copy(
        self, query: str, path: Path, format_name: str, options: dict[str, Any]
    ) -> None:
        raise NotImplementedError

    def validate_sql(self, text: str) -> str:
        raise NotImplementedError


class HarlequinSqliteAdapter(HarlequinAdapter):
    ADAPTER_OPTIONS: list[HarlequinAdapterOption] | None = SQLITE_OPTIONS
    COPY_FORMATS: list[HarlequinCopyFormat] | None = None

    def __init__(
        self,
        conn_str: Sequence[str],
        read_only: bool = False,
        connection_mode: Literal["ro", "rw", "rwc", "memory"] | None = None,
        timeout: str | float = 5.0,
        detect_types: str | int = 0,
        isolation_level: (
            Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] | None
        ) = "DEFERRED",
        cached_statements: str | int = 128,
        **_: Any,
    ) -> None:
        try:
            self.conn_str = conn_str if conn_str else (":memory:",)
            self.read_only = bool(read_only)
            self.connection_mode = connection_mode
            self.timeout = float(timeout)
            self.detect_types = int(detect_types)
            self.isolation_level = isolation_level
            self.cached_statements = int(cached_statements)
        except (ValueError, TypeError) as e:
            raise HarlequinConfigError(
                msg=f"SQLite adapter received bad config value: {e}",
                title="Harlequin could not initialize the selected adapter.",
            ) from e

    def connect(self) -> HarlequinSqliteConnection:
        if (
            self.read_only
            and self.connection_mode is not None
            and self.connection_mode != "ro"
        ):
            raise HarlequinConnectionError(
                "Cannot specify readonly flag and a connection mode."
            )
        elif self.read_only:
            mode_str = "?mode=ro"
        elif self.connection_mode is not None:
            mode_str = f"?mode={self.connection_mode}"
        else:
            mode_str = ""

        # build db strings
        db_uris: list[str] = []
        db_names: list[str] = []
        for s in self.conn_str:
            if s == ":memory:":
                db_uris.append(s)
                db_names.append("memory")
            elif s.startswith("file:"):
                db_uris.append(s)
                db_names.append(Path(unquote(urlparse(s).path)).stem)
            else:
                try:
                    p = Path(s).resolve()
                    db = f"{p.as_uri()}{mode_str}"
                    db_uris.append(db)
                    db_names.append(p.stem)
                except ValueError as e:
                    raise HarlequinConnectionError(
                        f"Cannot build URI from connection string {s}.",
                        title="SQLite couldn't connect to your database.",
                    ) from e
        primary_db, *other_dbs = db_uris
        try:
            conn = sqlite3.connect(
                database=primary_db,
                timeout=self.timeout,
                detect_types=self.detect_types,
                isolation_level=self.isolation_level,
                cached_statements=self.cached_statements,
                check_same_thread=False,
                uri=True,
            )
            # sqlite won't error on connect if you open a file that isn't a sqlite db
            # running pragma database_list will raise, though:
            _ = conn.execute("pragma database_list")
        except sqlite3.Error as e:
            if "file is not a database" in (msg := str(e)):
                msg = (
                    "sqlite raised the following error when trying to open "
                    f"{primary_db}:\n---\n{msg}\n---\n\n"
                    "Did you mean to use Harlequin's DuckDB adapter instead? "
                    f"Maybe try:\nharlequin -a duckdb {' '.join(self.conn_str)}\n"
                    f"or:\nharlequin -P None {' '.join(self.conn_str)}"
                )
            raise HarlequinConnectionError(
                msg=msg,
                title="SQLite couldn't connect to your database.",
            ) from e

        for uri, name in zip(other_dbs, db_names[1:]):
            try:
                conn.execute(f"attach database '{uri}' as {name}")
            except sqlite3.Error as e:
                if "file is not a database" in (msg := str(e)):
                    msg = (
                        "sqlite raised the following error when trying to open "
                        f"{uri}:\n---\n{msg}\n---\n\n"
                        "Did you mean to use Harlequin's DuckDB adapter instead? "
                        f"Maybe try:\nharlequin -a duckdb {' '.join(self.conn_str)}\n"
                        f"or:\nharlequin -P None {' '.join(self.conn_str)}"
                    )
                raise HarlequinConnectionError(
                    msg=msg, title="SQLite couldn't connect to your database."
                ) from e
        return HarlequinSqliteConnection(conn=conn)
