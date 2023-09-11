from dataclasses import dataclass
from typing import Literal, Union


@dataclass
class CSVOptions:
    # https://duckdb.org/docs/sql/statements/copy#csv-options
    compression: Literal["gzip", "zstd", "none", "auto"] = "auto"
    force_quote: bool = False
    dateformat: str = ""
    sep: str = ","
    quote: str = '"'
    escape: str = '"'
    header: bool = False
    nullstr: str = ""
    timestampformat: str = ""
    encoding: str = "UTF8"


@dataclass
class ParquetOptions:
    # https://duckdb.org/docs/sql/statements/copy#parquet-options
    compression: Literal["snappy", "gzip", "ztd"] = "snappy"
    # not yet supported in python API
    # row_group_size: int = 122880
    # field_ids: Optional[Union[Literal["auto"], Dict[str, int]]] = None


@dataclass
class JSONOptions:
    # https://duckdb.org/docs/sql/statements/copy#json-options
    compression: Literal["gzip", "zstd", "uncompressed", "auto"] = "auto"
    dateformat: str = ""
    timestampformat: str = ""
    array: bool = False


ExportOptions = Union[CSVOptions, ParquetOptions, JSONOptions]
