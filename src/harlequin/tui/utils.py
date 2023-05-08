COLUMN_TYPE_MAPPING = {
    "BIGINT": "##",
    "BIT": "010",
    "BOOLEAN": "t/f",
    "BLOB": "0b",
    "DATE": "d",
    "DOUBLE": "#.#",
    "DECIMAL": "#.#",
    "HUGEINT": "###",
    "INTEGER": "#",
    "INTERVAL": "|-|",
    "REAL": "#.#",
    "SMALLINT": "#",
    "TIME": "t",
    "TIMESTAMP": "ts",
    "TIMESTAMP WITH TIME ZONE": "ttz",
    "TINYINT": "#",
    "UBIGINT": "u##",
    "UINTEGER": "u#",
    "USMALLINT": "u#",
    "UTINYINT": "u#",
    "UUID": "uid",
    "VARCHAR": "s",
    "LIST": "[]",
    "STRUCT": "{}",
    "MAP": "{}",
}


def short_type(native_type: str) -> str:
    return COLUMN_TYPE_MAPPING.get(native_type.split("(")[0], "?")
