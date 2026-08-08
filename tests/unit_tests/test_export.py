from __future__ import annotations

import io
from pathlib import Path

import pyarrow as pa
import pytest

from harlequin.exception import HarlequinCopyError
from harlequin.export import file_format_names, write_file, write_stream

TEXT_FORMATS = ["csv", "tsv", "json", "jsonl", "ndjson"]
BINARY_FORMATS = ["parquet", "orc", "feather", "arrow"]


@pytest.fixture
def data() -> pa.Table:
    return pa.table({"a": [1, 2], "b": ["x,y", None]})


def to_bytes(data: pa.Table, format_name: str, options: dict | None = None) -> bytes:
    out = io.BytesIO()
    write_stream(data, out, format_name, options)
    return out.getvalue()


class TestFormatNames:
    def test_every_name_writes_something(self, data: pa.Table, tmp_path: Path) -> None:
        for name in file_format_names():
            path = tmp_path / f"out.{name}"
            write_file(data, path, name)
            assert path.stat().st_size > 0

    def test_an_unknown_format_names_the_known_ones(self, data: pa.Table) -> None:
        with pytest.raises(HarlequinCopyError, match="csv, tsv, json"):
            write_file(data, Path("out.yaml"), "yaml")


class TestVariants:
    """`tsv` and `jsonl` are the csv and json writers under other defaults, not
    new writers -- so what they get right is exactly what csv and json got
    right, quoting and type rendering included."""

    def test_tsv_is_csv_with_a_tab(self, data: pa.Table) -> None:
        assert to_bytes(data, "tsv") == to_bytes(data, "csv", {"sep": "\t"})

    def test_json_is_an_array_of_row_objects(self, data: pa.Table) -> None:
        assert to_bytes(data, "json") == (
            b'[\n\t{"a":1,"b":"x,y"},\n\t{"a":2,"b":null}\n]\n'
        )

    def test_jsonl_is_one_object_per_line(self, data: pa.Table) -> None:
        assert to_bytes(data, "jsonl") == b'{"a":1,"b":"x,y"}\n{"a":2,"b":null}\n'

    def test_ndjson_is_jsonl(self, data: pa.Table) -> None:
        assert to_bytes(data, "ndjson") == to_bytes(data, "jsonl")

    def test_arrow_is_feather(self, data: pa.Table) -> None:
        assert to_bytes(data, "arrow") == to_bytes(data, "feather")


class TestOptions:
    def test_an_explicit_option_beats_the_formats_default(self, data: pa.Table) -> None:
        """The copy dialog passes every option of a format, set or not, and its
        array flag defaults off -- so `json` must not force an array on it."""
        assert to_bytes(data, "json", {"array": False}) == to_bytes(data, "jsonl")
        assert to_bytes(data, "jsonl", {"array": True}) == to_bytes(data, "json")

    def test_false_is_a_value_and_empty_is_not(self, data: pa.Table) -> None:
        """`header=False` has to reach the writer; a text option the user never
        typed into arrives as "" and must not."""
        assert to_bytes(data, "csv", {"header": False}) == b'1,"x,y"\n2,\n'
        assert to_bytes(data, "csv", {"sep": ""}) == to_bytes(data, "csv")

    def test_header_is_on_by_default(self, data: pa.Table) -> None:
        assert to_bytes(data, "csv").startswith(b"a,b\n")

    def test_null_string(self, data: pa.Table) -> None:
        assert to_bytes(data, "csv", {"na_rep": "\\N"}) == b'a,b\n1,"x,y"\n2,\\N\n'

    def test_force_quoting(self, data: pa.Table) -> None:
        assert to_bytes(data, "csv", {"quoting": True}).startswith(b'"a","b"\n')
        assert to_bytes(data, "csv", {"quoting": False}) == to_bytes(data, "csv")


class TestColumnNames:
    def test_duplicate_names_are_made_unique(self) -> None:
        """`select 1 as a, 2 as a` is legal SQL and a legal Arrow table. It is
        not something duckdb will export, or that a csv header can say."""
        data = pa.Table.from_arrays(
            [pa.array([1]), pa.array([2]), pa.array([3])], names=["a", "a", "a"]
        )
        assert to_bytes(data, "csv") == b"a,a_0,a_1\n1,2,3\n"

    def test_names_that_are_already_unique_are_left_alone(self, data: pa.Table) -> None:
        assert to_bytes(data, "csv").startswith(b"a,b\n")


class TestEmptyResults:
    """Zero rows is not an error: an empty file is how "the query matched
    nothing" is told apart from "the query failed"."""

    @pytest.fixture
    def empty(self) -> pa.Table:
        return pa.table({"a": pa.array([], type=pa.int64())})

    def test_csv_keeps_its_header(self, empty: pa.Table) -> None:
        assert to_bytes(empty, "csv") == b"a\n"

    def test_jsonl_is_empty(self, empty: pa.Table) -> None:
        assert to_bytes(empty, "jsonl") == b""

    @pytest.mark.parametrize("format_name", TEXT_FORMATS + BINARY_FORMATS)
    def test_no_format_raises(
        self, empty: pa.Table, tmp_path: Path, format_name: str
    ) -> None:
        write_file(empty, tmp_path / "out", format_name)


class TestStream:
    @pytest.mark.parametrize("format_name", TEXT_FORMATS + BINARY_FORMATS)
    def test_a_stream_and_a_file_are_the_same_bytes(
        self, data: pa.Table, tmp_path: Path, format_name: str
    ) -> None:
        """`hsql -o out.csv` and `hsql > out.csv` have to produce the same file,
        and the temp-file copy is the one thing that could get that wrong."""
        path = tmp_path / "out"
        write_file(data, path, format_name)
        assert to_bytes(data, format_name) == path.read_bytes()

    @pytest.mark.parametrize("format_name", TEXT_FORMATS)
    def test_newlines_are_not_translated(
        self, data: pa.Table, format_name: str
    ) -> None:
        """duckdb writes \\n. The copy out is binary so that Python's text-mode
        translation cannot turn it into \\r\\n on Windows."""
        assert b"\r\n" not in to_bytes(data, format_name)
