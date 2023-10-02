from . import MemoryPool, Scalar, _PandasConvertible

class Expression: ...
class ScalarAggregateOptions: ...

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
