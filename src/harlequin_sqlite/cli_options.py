from __future__ import annotations

from harlequin.options import (
    FlagOption,
    SelectOption,
    TextOption,
)

read_only = FlagOption(
    name="read-only",
    short_decls=["-readonly", "-r"],
    description="Open the database file in read-only mode.",
)
connection_mode = SelectOption(
    name="mode",
    short_decls=["-mode", "-m"],
    description=(
        "The mode parameter may be set to either 'ro', 'rw', 'rwc', or 'memory'. "
        "Attempting to set it to any other value is an error. If 'ro' is specified, "
        "then the database is opened for read-only access. If the mode option is set "
        "to 'rw', then the database is opened for read-write (but not create) access. "
        "Value 'rwc' is equivalent to setting both SQLITE_OPEN_READWRITE and "
        "SQLITE_OPEN_CREATE. If the mode option is set to 'memory' then a pure "
        "in-memory database that never reads or writes from disk is used. "
        "It is an error to specify a value for the mode parameter that is less "
        "restrictive than the separate --read-only option."
    ),
    choices=["ro", "rw", "rwc", "memory"],
    default="rwc",
)


def _float_validator(s: str | None) -> tuple[bool, str]:
    if s is None:
        return True, ""
    try:
        _ = float(s)
    except ValueError:
        return False, f"Cannot convert {s} to a float!"
    else:
        return True, ""


timeout = TextOption(
    name="timeout",
    description=(
        "How many seconds the connection should wait before raising an "
        "OperationalError when a table is locked. If another connection opens a "
        "transaction to modify a table, that table will be locked until the "
        "transaction is committed. Default five seconds."
    ),
    validator=_float_validator,
)


def _int_validator(s: str | None) -> tuple[bool, str]:
    if s is None:
        return True, ""
    try:
        _ = int(s)
    except ValueError:
        return False, f"Cannot convert {s} to an int!"
    else:
        return True, ""


detect_types = TextOption(
    name="detect-types",
    description=(
        "Control whether and how data types not natively supported by SQLite are "
        "looked up to be converted to Python types, using the converters registered "
        "with register_converter(). Set it to any combination (using |, bitwise or) "
        "of PARSE_DECLTYPES and PARSE_COLNAMES to enable this. Column names takes "
        "precedence over declared types if both flags are set. Types cannot be "
        "detected for generated fields (for example max(data)), even when the "
        "detect_types parameter is set; str will be returned instead. By default (0), "
        "type detection is disabled."
    ),
    validator=_int_validator,
)

isolation_level = SelectOption(
    name="isolation-level",
    description=(
        "Control legacy transaction handling behaviour. See Connection.isolation_level "
        "and Transaction control via the isolation_level attribute for more "
        'information. Can be "DEFERRED" (default), "EXCLUSIVE" or "IMMEDIATE"; or None '
        "to disable opening transactions implicitly. Has no effect unless "
        "Connection.autocommit is set to LEGACY_TRANSACTION_CONTROL (the default)."
    ),
    choices=["DEFERRED", "EXCLUSIVE", "IMMEDIATE"],
    default="DEFERRED",
)


cached_statements = TextOption(
    name="cached-statements",
    description=(
        "The number of statements that sqlite3 should internally cache for this "
        "connection, to avoid parsing overhead. By default, 128 statements."
    ),
    validator=_int_validator,
)

SQLITE_OPTIONS = [
    read_only,
    connection_mode,
    timeout,
    detect_types,
    isolation_level,
    cached_statements,
]
