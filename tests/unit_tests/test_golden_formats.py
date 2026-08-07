"""Every format, against one result set holding every type that is hard.

The point of routing both files and text layouts through duckdb is that a value
looks the same however it leaves: `-F table` and `-F csv` are supposed to agree
cell for cell, and a Postgres timestamp and a DuckDB timestamp are supposed to
agree with each other. Neither property is visible from inside one format, so
these are golden files -- what changes here is a change to Harlequin's output
contract, and it should be read in a diff rather than discovered by an agent.

Regenerate by running this module under `HARLEQUIN_UPDATE_GOLDEN=1`, and read
the diff before committing it.
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Callable

import duckdb
import pytest

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

FILE_FORMATS = ["csv", "tsv", "json", "jsonl"]
LAYOUTS = ["table", "markdown", "vertical"]


@pytest.fixture(scope="module", autouse=True)
def utc() -> None:
    """Pin the connection that does the serializing.

    duckdb renders a `timestamptz` in the session's time zone, and the session
    here is duckdb's default connection -- the one both `write_stream()` and
    `text_columns()` reach through. Pinning it is what makes these files the
    same on a runner in Denver and a runner in Berlin.
    """
    duckdb.execute("set TimeZone='UTC'")


@pytest.fixture
def golden_dir(data_dir: Path) -> Path:
    return data_dir / "unit_tests" / "golden"


@pytest.fixture
def result(result_set: ResultSetFactory) -> ResultSet:
    return result_set(TYPE_COVERAGE_SQL)


def assert_golden(path: Path, actual: bytes) -> None:
    if os.environ.get("HARLEQUIN_UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(actual)
    assert path.is_file(), f"{path.name} has never been generated."
    assert actual.decode() == path.read_bytes().decode()


@pytest.mark.parametrize("format_name", FILE_FORMATS)
def test_file_formats(result: ResultSet, golden_dir: Path, format_name: str) -> None:
    out = io.BytesIO()
    write_stream(result.arrow_table(), out, format_name)
    assert_golden(golden_dir / f"type_coverage.{format_name}", out.getvalue())


@pytest.mark.parametrize("name", LAYOUTS)
def test_layouts(result: ResultSet, golden_dir: Path, name: str) -> None:
    out = io.StringIO()
    get_layout(name).write(result, out)
    assert_golden(golden_dir / f"type_coverage.{name}.txt", out.getvalue().encode())


def test_the_layouts_and_the_files_agree_cell_for_cell(result: ResultSet) -> None:
    """The reason `text_columns()` casts through duckdb rather than calling
    `str()`: every string `-F table` prints is a string `-F csv` writes.

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
