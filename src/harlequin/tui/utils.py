from typing import Union

from duckdb.typing import DuckDBPyType

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


def short_type(native_type: Union[DuckDBPyType, str]) -> str:
    """
    In duckdb v0.8.0, relation.dtypes started returning a DuckDBPyType,
    instead of a string. However, this type isn't an ENUM, and there
    aren't classes for all types, so it's hard
    to check class members. So we just convert to a string and split
    complex types on their first paren to match our dictionary.
    """
    return COLUMN_TYPE_MAPPING.get(str(native_type).split("(")[0], "?")
