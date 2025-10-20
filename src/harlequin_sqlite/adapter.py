from __future__ import annotations

import sqlite3
from contextlib import suppress
from itertools import cycle, zip_longest
from pathlib import Path
from typing import Any, Literal, Sequence
from urllib.parse import unquote, urlparse

from textual_fastdatatable.backend import AutoBackendType

from harlequin.adapter import HarlequinAdapter, HarlequinConnection, HarlequinCursor
from harlequin.autocomplete.completion import HarlequinCompletion
from harlequin.catalog import Catalog, CatalogItem
from harlequin.exception import (
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinQueryError,
)
from harlequin.options import HarlequinAdapterOption, HarlequinCopyFormat
from harlequin.transaction_mode import HarlequinTransactionMode
from harlequin_sqlite.catalog import DatabaseCatalogItem
from harlequin_sqlite.cli_options import SQLITE_OPTIONS
from harlequin_sqlite.completions import get_completion_data

IN_MEMORY_CONN_STR = (":memory:",)


class HarlequinSqliteCursor(HarlequinCursor):
    def __init__(self, conn: HarlequinSqliteConnection, cur: sqlite3.Cursor) -> None:
        self.conn = conn
        self.cur = cur
        self._limit: int | None = None
        try:
            _first_row = cur.fetchone()
        except sqlite3.Error:  # maybe canceled query here
            _first_row = None
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
            try:
                remaining_rows = (
                    self.cur.fetchall()
                    if self._limit is None
                    else self.cur.fetchmany(self._limit - 1)
                )
            except sqlite3.OperationalError:  # maybe canceled here
                return None
            except sqlite3.Error as e:
                raise HarlequinQueryError(
                    msg=str(e),
                    title=(
                        "SQLite raised an error when fetching results for your query:"
                    ),
                ) from e
            return [self._first_row, *remaining_rows]
        else:
            return None

    def fetchone(self) -> tuple | None:
        return self._first_row


class HarlequinSqliteConnection(HarlequinConnection):
    def __init__(self, conn: sqlite3.Connection, init_message: str = "") -> None:
        self.conn = conn
        self.init_message = init_message
        self._transaction_modes: list[HarlequinTransactionMode | None] = (
            [
                HarlequinTransactionMode(label="Auto"),
                HarlequinTransactionMode(
                    label="Manual", commit=self.conn.commit, rollback=self.conn.rollback
                ),
            ]
            if hasattr(conn, "autocommit")
            else [None]
        )
        self._transaction_mode_gen = cycle(self._transaction_modes)
        self._transaction_mode = next(self._transaction_mode_gen)
        self._sync_connection_transaction_mode()

    def execute(self, query: str) -> HarlequinSqliteCursor | None:
        # the behavior on manual mode is really counter-intuitive; if a
        # transaction isn't explicitly began, it's basically the same as
        # auto. By forcing an explicit begin, the behavior is more like
        # manual mode on other databases.
        if (
            self.transaction_mode
            and self.transaction_mode.label == "Manual"
            and not self.conn.in_transaction
        ):
            with suppress(sqlite3.Error):
                self.conn.execute("begin;")
        try:
            cur = self.conn.execute(query)
        except sqlite3.Error as e:
            # no-op if user ran a begin; statement, since we already started the txn
            if (
                isinstance(e, sqlite3.OperationalError)
                and str(e) == "cannot start a transaction within a transaction"
            ):
                return None
            raise HarlequinQueryError(
                msg=str(e),
                title="SQLite raised an error when compiling or running your query:",
            ) from e

        if cur.description is not None:
            return HarlequinSqliteCursor(conn=self, cur=cur)
        else:
            return None

    def cancel(self) -> None:
        self.conn.interrupt()

    def get_catalog(self) -> Catalog:
        catalog_items: list[CatalogItem] = []
        databases = self._get_databases()
        for database_label in databases:
            catalog_items.append(
                DatabaseCatalogItem.from_label(label=database_label, connection=self)
            )
        return Catalog(items=catalog_items)

    def get_completions(self) -> list[HarlequinCompletion]:
        return get_completion_data(self.conn)

    @property
    def transaction_mode(self) -> HarlequinTransactionMode | None:
        return self._transaction_mode

    def toggle_transaction_mode(self) -> HarlequinTransactionMode | None:
        new_mode = next(self._transaction_mode_gen)
        self._transaction_mode = new_mode
        self._sync_connection_transaction_mode()
        return new_mode

    def close(self) -> None:
        self.conn.close()

    def _sync_connection_transaction_mode(self) -> None:
        if not self._transaction_mode or not hasattr(self.conn, "autocommit"):
            return

        if self.conn.in_transaction:
            self.conn.commit()

        if self.transaction_mode and self.transaction_mode.label == "Auto":
            self.conn.autocommit = True
        else:
            self.conn.autocommit = False

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
    IMPLEMENTS_CANCEL = True
    ADAPTER_DETAILS = "This is an SQLite adapter part of Harlequin core."

    def __init__(
        self,
        conn_str: Sequence[str],
        init_path: Path | str | None = None,
        no_init: bool | str = False,
        read_only: bool = False,
        connection_mode: Literal["ro", "rw", "rwc", "memory"] | None = None,
        timeout: str | float = 5.0,
        detect_types: str | int = 0,
        isolation_level: Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] = "DEFERRED",
        cached_statements: str | int = 128,
        extension: list[str] | None = None,
        **_: Any,
    ) -> None:
        try:
            self.conn_str = (
                conn_str
                if conn_str and conn_str != ("",) and connection_mode != "memory"
                else IN_MEMORY_CONN_STR
            )
            self.init_path = (
                Path(init_path).expanduser().resolve()
                if init_path is not None
                else Path.home() / ".sqliterc"
            )
            self.no_init = bool(no_init)
            self.read_only = bool(read_only)
            self.connection_mode = connection_mode
            self.timeout = float(timeout)
            self.detect_types = int(detect_types)
            self.isolation_level = isolation_level
            self.cached_statements = int(cached_statements)
            self.extensions = extension if extension is not None else []
            self.can_load_extensions = hasattr(
                sqlite3.Connection, "enable_load_extension"
            )
        except (ValueError, TypeError) as e:
            raise HarlequinConfigError(
                msg=f"SQLite adapter received bad config value: {e}",
                title="Harlequin could not initialize the selected adapter.",
            ) from e

        if self.extensions and not self.can_load_extensions:
            raise HarlequinConfigError(
                title="Harlequin could not initialize the selected adapter.",
                msg=(
                    "SQLite adapter received --extension option, but extensions "
                    "are disabled on this SQLite distribution. See "
                    "https://harlequin.sh/docs/sqlite/extensions"
                ),
            )

    @property
    def connection_id(self) -> str | None:
        if self.conn_str == IN_MEMORY_CONN_STR:
            return ""
        return ",".join(
            [Path(conn).resolve().as_posix() for conn in sorted(self.conn_str)]
        )

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

        # Python 3.12 added an autocommit attribute, which should be
        # turned on by default
        if hasattr(conn, "autocommit"):
            conn.autocommit = True

        for uri, name in zip(other_dbs, db_names[1:], strict=False):
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

        if self.can_load_extensions:
            conn.enable_load_extension(True)

        for extension in self.extensions:
            try:
                conn.load_extension(extension)
            except sqlite3.Error as e:
                raise HarlequinConnectionError(
                    str(e), title="SQLite couldn't load your extension."
                ) from e

        init_msg = ""
        if self.init_path is not None and not self.no_init:
            init_script = self._read_init_script(self.init_path)
            count = 0
            for command in self._split_script(init_script):
                rewritten_command = self._rewrite_init_command(command)
                for cmd in rewritten_command.split(";"):
                    if cmd.strip():
                        try:
                            conn.execute(cmd)
                        except sqlite3.Error as e:
                            msg = (
                                f"Attempted to execute script at {self.init_path}. "
                                f"Contents:\n{command}\nRewritten to:\n"
                                f"{rewritten_command}\nCurrently executing:\n"
                                f"{cmd}\nError:\n{e}"
                            )
                            if not self.can_load_extensions and isinstance(
                                e, sqlite3.OperationalError
                            ):
                                msg += (
                                    "\nWarning: Cannot load extensions with this "
                                    "SQLite distribution. See "
                                    "https://harlequin.sh/docs/sqlite/extensions"
                                )
                            raise HarlequinConnectionError(
                                msg,
                                title=(
                                    "SQLite could not execute your initialization "
                                    "script."
                                ),
                            ) from e
                        else:
                            count += 1
            if count > 0:
                init_msg = (
                    f"Executed {count} {'command' if count == 1 else 'commands'} "
                    f"from {self.init_path}"
                )
        self.ADAPTER_DRIVER_DETAILS = f"""
Connected to database `{primary_db}`
        """
        return HarlequinSqliteConnection(conn=conn, init_message=init_msg)

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
        SQLite init scripts can contain SQL queries or dot commands. The SQL
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
            # queries can be terminated by / or "go" on a line
            # by itself
            elif line in ("/", "go"):
                commands.append("\n".join(lines[i:j]))
                i = j + 1
        commands.append("\n".join(lines[i:]))
        return [command.strip() for command in commands if command]

    @classmethod
    def _rewrite_init_command(cls, command: str) -> str:
        """
        SQLite init scripts can contain dot commands, which can only be executed
        by the CLI, not the python API. Here, we rewrite some common ones into
        SQL, and rewrite the others to be no-ops.
        """
        if not command.startswith("."):
            return command
        elif command.startswith(".open"):
            return cls._rewrite_dot_open(command)
        elif command.startswith(".load"):
            return cls._rewrite_dot_load(command)
        else:
            return ""

    @staticmethod
    def _rewrite_dot_open(command: str) -> str:
        """
        Rewrites .open command into its approx SQL equivalent.
        (This attaches databases instead of opening a new connection).

        Options (--readonly, etc.) are not supported.
        """
        args = command.split()[1:]
        if not args:
            return "attach ''"
        elif args[-1] == ":memory:":
            return "attach ':memory:';"
        else:
            # options are not supported.
            db_path = Path(args[-1])
            return f"attach '{db_path}' as {db_path.stem};"

    @staticmethod
    def _rewrite_dot_load(command: str) -> str:
        """
        Rewrites .load command into its SQL equivalent, load_extension().
        """
        args = command.split()[1:]
        if not args:
            raise sqlite3.ProgrammingError("Could not execute .load with no args.")
        elif len(args) == 1:
            return f"select load_extension('{args[0]}');"
        elif len(args) == 2:
            return f"select load_extension('{args[0]}', '{args[1]}');"
        else:
            raise sqlite3.ProgrammingError(
                f"Could not execute .load with following args: {args}"
            )
