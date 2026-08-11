"""Choosing what a result set is written as, and writing it.

Two families of format, and the difference between them is who serializes.
`harlequin.export` hands an Arrow table to duckdb or pyarrow; `harlequin.layout`
arranges the strings `ResultSet.text_columns()` already produced. Neither is
reimplemented here, and nothing here renders a value itself -- which is what
makes `--format table` and `--format csv` agree cell for cell.

Everything leaves through one binary stream, `-o PATH` included, so a file and
a redirect cannot disagree about a byte. Text is encoded UTF-8 explicitly: the
bytes are the contract, and a console's code page is not part of it.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any, BinaryIO, Mapping

from harlequin.export import file_format_names, write_stream
from harlequin.layout import get_layout, layout_names

if TYPE_CHECKING:
    from harlequin.layout import LayoutOptions
    from harlequin.query import ResultSet

NONE = "none"
"""Discard the rows and report status only. For DDL, DML and ETL."""

_HOLD_MANY = frozenset({*layout_names(), "jsonl", "ndjson", NONE})


def format_names() -> list[str]:
    """Every name `--format` accepts, aliases included, in the order help lists them."""
    return [*layout_names(), *file_format_names(), NONE]


def is_layout(format_name: str) -> bool:
    """Whether `harlequin.layout` arranges this format, rather than a writer.

    Which is what decides whose options apply to it: the row cap and the psql
    switches are the layouts', and a file format has neither.
    """
    return format_name in layout_names()


def holds_many(format_name: str) -> bool:
    """Whether a format can hold more than one result set.

    Two headers in one csv is silent corruption of the same family as silent
    truncation, so a format that cannot hold a second result set says so
    instead. Newline-delimited json can: another result set is more lines.
    """
    return format_name in _HOLD_MANY


def write(
    result: "ResultSet",
    format_name: str,
    out: BinaryIO,
    *,
    layout_options: "LayoutOptions",
    file_options: Mapping[str, Any],
) -> None:
    """Write one result set to an open binary stream."""
    if format_name == NONE:
        return
    if format_name in layout_names():
        text = io.StringIO()
        get_layout(format_name, layout_options).write(result, text)
        out.write(text.getvalue().encode("utf-8"))
    else:
        write_stream(result.arrow_table(), out, format_name, file_options)
