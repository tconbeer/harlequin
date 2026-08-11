"""Every format, against one result set holding every type that is hard.

The point of routing both files and text layouts through duckdb is that a value
looks the same however it leaves: `--format table` and `--format csv` are
supposed to agree cell for cell, and a Postgres timestamp and a DuckDB timestamp
are supposed to agree with each other. Neither property is visible from inside
one format, so these are snapshots -- what changes here is a change to
Harlequin's output contract, and it should be read in a diff rather than
discovered by an agent.

They are single-file snapshots, one per format, in binary write mode: the bytes
are the contract, so nothing here may re-encode a newline on the way to disk or
back. Regenerate them the way every other snapshot in this repo is regenerated,
with `--snapshot-update` on Python 3.10, and read the diff before committing it.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Callable

import duckdb
import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.single_file import SingleFileSnapshotExtension

from harlequin.export import write_stream
from harlequin.layout import get_layout
from harlequin.query import ResultSet

ResultSetFactory = Callable[..., ResultSet]

TYPE_COVERAGE_SQL = """
select
    1::integer as id,
    'ünïcode, "quoted"' as label,
    12345.6789::decimal(18, 4) as amount,
    1234567.89::double as approx,
    true as flag,
    date '2024-03-01' as day,
    timestamp '2024-03-01 12:30:00.123456' as at,
    timestamptz '2024-03-01 12:30:00+00' as at_tz,
    interval '1 day 2 hours' as span,
    '\\x00\\x01\\xFF'::blob as payload,
    [1, 2, 3] as items,
    {'a': 1, 'b': 'two'} as record,
    map([1, 2], ['one', 'two']) as lookup
union all
select
    2, 'a value considerably wider than its column name', null, null, false,
    null, null, null, null, null, null, null, null
order by id
"""

LAYOUTS = ["table", "markdown", "vertical"]


# One snapshot file per format, named like the format, so a reviewer opening
# the diff sees a csv as a csv. The default `.raw` would make every one of them
# look alike.
class CsvSnapshot(SingleFileSnapshotExtension):
    file_extension = "csv"


class TsvSnapshot(SingleFileSnapshotExtension):
    file_extension = "tsv"


class JsonSnapshot(SingleFileSnapshotExtension):
    file_extension = "json"


class JsonlSnapshot(SingleFileSnapshotExtension):
    file_extension = "jsonl"


class TextSnapshot(SingleFileSnapshotExtension):
    file_extension = "txt"


FILE_FORMATS = {
    "csv": CsvSnapshot,
    "tsv": TsvSnapshot,
    "json": JsonSnapshot,
    "jsonl": JsonlSnapshot,
}


@pytest.fixture(scope="module", autouse=True)
def utc() -> None:
    """Pin the connection that does the serializing.

    duckdb renders a `timestamptz` in the session's time zone, and the session
    here is duckdb's default connection -- the one both `write_stream()` and
    `text_columns()` reach through. Pinning it is what makes these snapshots the
    same on a runner in Denver and a runner in Berlin.
    """
    duckdb.execute("set TimeZone='UTC'")


@pytest.fixture
def result(result_set: ResultSetFactory) -> ResultSet:
    return result_set(TYPE_COVERAGE_SQL)


@pytest.mark.parametrize("format_name", list(FILE_FORMATS))
def test_file_formats(
    result: ResultSet, snapshot: SnapshotAssertion, format_name: str
) -> None:
    out = io.BytesIO()
    write_stream(result.arrow_table(), out, format_name)
    assert out.getvalue() == snapshot(extension_class=FILE_FORMATS[format_name])


@pytest.mark.parametrize("name", LAYOUTS)
def test_layouts(result: ResultSet, snapshot: SnapshotAssertion, name: str) -> None:
    out = io.StringIO()
    get_layout(name).write(result, out)
    assert out.getvalue().encode() == snapshot(extension_class=TextSnapshot)


def test_the_snapshots_were_checked_out_with_lf(request: pytest.FixtureRequest) -> None:
    """Otherwise every line of every snapshot above differs, invisibly.

    Windows checkouts default to `core.autocrlf`, and these are compared byte
    for byte. `.gitattributes` pins them; this says so when it hasn't.
    """
    if request.config.option.update_snapshots:
        # syrupy writes the collection at the end of the session, so there is
        # nothing on disk to look at yet -- and it is about to be rewritten.
        pytest.skip("snapshots are rewritten at the end of this run")

    directory = Path(request.path).parent / "__snapshots__" / Path(request.path).stem
    snapshots = sorted(directory.iterdir())
    assert snapshots, f"no snapshots in {directory}"
    mangled = [p.name for p in snapshots if b"\r\n" in p.read_bytes()]
    assert not mangled, (
        f"{mangled} were checked out with CRLF line endings. Newlines are part "
        "of what these snapshots assert; .gitattributes pins them to LF."
    )


def test_the_layouts_and_the_files_agree_cell_for_cell(result: ResultSet) -> None:
    """The reason `text_columns()` casts through duckdb rather than calling
    `str()`: every string `--format table` prints is a string `--format csv`
    writes.

    Parsed back with the stdlib reader, not split on the delimiter: quoting is
    csv's framing of a value, not part of it, and unwrapping it is the only way
    to compare the values themselves.
    """
    out = io.BytesIO()
    write_stream(result.arrow_table(), out, "tsv", {"header": False, "na_rep": "∅"})
    from_csv = list(csv.reader(out.getvalue().decode().splitlines(), delimiter="\t"))

    text = result.text_columns()
    from_text = [
        ["∅" if value is None else value for value in row]
        for row in zip(*[column.to_pylist() for column in text.columns], strict=False)
    ]

    assert from_text == from_csv
