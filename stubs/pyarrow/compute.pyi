from __future__ import annotations

from . import MemoryPool, Scalar, _PandasConvertible
from .types import DataType

class Expression: ...
class ScalarAggregateOptions: ...

class CastOptions:
    def __init__(
        self,
        target_type: DataType | None = None,
        allow_int_overflow: bool | None = None,
        allow_time_truncate: bool | None = None,
        allow_time_overflow: bool | None = None,
        allow_decimal_truncate: bool | None = None,
        allow_float_truncate: bool | None = None,
        allow_invalid_utf8: bool | None = None,
    ) -> None: ...

def max(  # noqa: A001
    array: _PandasConvertible,
    /,
    *,
    skip_nulls: bool = True,
    min_count: int = 1,
    options: ScalarAggregateOptions | None = None,
    memory_pool: MemoryPool | None = None,
) -> Scalar: ...
def utf8_length(
    strings: _PandasConvertible, /, *, memory_pool: MemoryPool | None = None
) -> _PandasConvertible: ...
