from __future__ import annotations

import datetime as dt
import io
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.feather as pf
import pyarrow.orc as po
import pyarrow.parquet as pq
import pytest
from textual_fastdatatable.backend import create_backend

from harlequin.copy_formats import HARLEQUIN_COPY_FORMATS
from harlequin.exception import HarlequinCopyError
from harlequin.export import (
    _deduplicate_column_names,
    file_format_names,
    write_file,
    write_stream,
)
from harlequin.options import HarlequinCopyFormat, SelectOption

TEXT_FORMATS = ["csv", "tsv", "json", "jsonl", "ndjson"]
BINARY_FORMATS = ["parquet", "orc", "feather", "arrow"]
GZIP_MAGIC = b"\x1f\x8b"

NEEDS_ORC_READER = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "pyarrow's ORC reader wants the IANA time zone database, which the "
        "Windows runners do not have. Writing ORC there is fine, and the tests "
        "that only write are not skipped."
    ),
)


@pytest.fixture
def data() -> pa.Table:
    return pa.table({"a": [1, 2], "b": ["x,y", None]})


@pytest.fixture
def dated() -> pa.Table:
    """A date and a timestamp, so the format options have something to format."""
    return pa.table(
        {
            "d": pa.array([dt.date(2024, 3, 1), None], type=pa.date32()),
            "ts": pa.array(
                [dt.datetime(2024, 3, 1, 12, 30, 5), None], type=pa.timestamp("us")
            ),
        }
    )


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


class TestOptionPrecedence:
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


class TestCsvOptions:
    """Every option the csv format declares, at a non-default value.

    csv and tsv share a writer, so this covers both.
    """

    def test_header(self, data: pa.Table) -> None:
        assert to_bytes(data, "csv").startswith(b"a,b\n")
        assert to_bytes(data, "csv", {"header": False}) == b'1,"x,y"\n2,\n'

    def test_sep(self, data: pa.Table) -> None:
        assert to_bytes(data, "csv", {"sep": "|"}) == b"a|b\n1|x,y\n2|\n"

    def test_quoting(self, data: pa.Table) -> None:
        assert to_bytes(data, "csv", {"quoting": True}).startswith(b'"a","b"\n')
        assert to_bytes(data, "csv", {"quoting": False}) == to_bytes(data, "csv")

    def test_quotechar(self, data: pa.Table) -> None:
        assert to_bytes(data, "csv", {"quotechar": "'"}) == b"a,b\n1,'x,y'\n2,\n"

    def test_escapechar(self, data: pa.Table) -> None:
        quoted = pa.table({"a": ['say "hi"']})
        assert to_bytes(quoted, "csv", {"escapechar": "\\"}) == b'a\n"say \\"hi\\""\n'

    def test_na_rep(self, data: pa.Table) -> None:
        assert to_bytes(data, "csv", {"na_rep": "\\N"}) == b'a,b\n1,"x,y"\n2,\\N\n'

    def test_date_format(self, dated: pa.Table) -> None:
        assert to_bytes(dated, "csv", {"date_format": "%m/%d/%Y"}).startswith(
            b"d,ts\n03/01/2024,"
        )

    def test_timestamp_format(self, dated: pa.Table) -> None:
        assert b",12:30\n" in to_bytes(dated, "csv", {"timestamp_format": "%H:%M"})

    def test_compression(self, data: pa.Table) -> None:
        assert to_bytes(data, "csv", {"compression": "gzip"}).startswith(GZIP_MAGIC)
        assert to_bytes(data, "csv", {"compression": "none"}) == to_bytes(data, "csv")

    def test_encoding(self, data: pa.Table) -> None:
        """Only UTF8 is supported, but it must still be accepted."""
        assert to_bytes(data, "csv", {"encoding": "UTF8"}) == to_bytes(data, "csv")


class TestJsonOptions:
    """Every option the json format declares. `jsonl` shares this writer."""

    def test_array(self, data: pa.Table) -> None:
        assert to_bytes(data, "json", {"array": True}).startswith(b"[\n")
        assert not to_bytes(data, "json", {"array": False}).startswith(b"[\n")

    def test_date_format(self, dated: pa.Table) -> None:
        assert b'"d":"03/01/2024"' in to_bytes(
            dated, "json", {"date_format": "%m/%d/%Y"}
        )

    def test_timestamp_format(self, dated: pa.Table) -> None:
        """This option was read under the wrong key and silently did nothing."""
        assert b'"ts":"12:30"' in to_bytes(dated, "json", {"timestamp_format": "%H:%M"})

    def test_compression(self, data: pa.Table) -> None:
        assert to_bytes(data, "json", {"compression": "gzip"}).startswith(GZIP_MAGIC)
        assert to_bytes(data, "json", {"compression": "uncompressed"}) == to_bytes(
            data, "json"
        )


class TestParquetOptions:
    @pytest.mark.parametrize(
        "codec", ["snappy", "gzip", "zstd", "uncompressed"], ids=lambda c: str(c)
    )
    def test_compression(self, data: pa.Table, tmp_path: Path, codec: str) -> None:
        path = tmp_path / "out.parquet"
        write_file(data, path, "parquet", {"compression": codec})
        written = pq.ParquetFile(str(path))
        assert written.metadata.row_group(0).column(0).compression == codec.upper()
        assert written.read().to_pydict() == data.to_pydict()


class TestOrcOptions:
    @NEEDS_ORC_READER
    @pytest.mark.parametrize("codec", ["UNCOMPRESSED", "SNAPPY", "ZLIB", "LZ4", "zstd"])
    def test_compression(self, data: pa.Table, tmp_path: Path, codec: str) -> None:
        path = tmp_path / "out.orc"
        write_file(data, path, "orc", {"compression": codec})
        assert po.read_table(str(path)).to_pydict() == data.to_pydict()

    def test_a_number_that_is_not_one_is_an_error(
        self, data: pa.Table, tmp_path: Path
    ) -> None:
        """This one never reaches pyarrow, so it runs everywhere."""
        with pytest.raises(HarlequinCopyError):
            write_file(data, tmp_path / "out.orc", "orc", {"batch_size": "lots"})

    @NEEDS_ORC_READER
    def test_a_rejected_bloom_filter_column_is_an_error(
        self, data: pa.Table, tmp_path: Path
    ) -> None:
        """pyarrow refuses every column name here, and does it with a
        ValueError -- which the export dialog does not catch, so before this
        was wrapped it took the app down rather than opening an error modal.

        Skipped where ORC needs a time zone database it doesn't have: the error
        raised there is that one, not the one under test, and the assertion
        would pass for the wrong reason.
        """
        with pytest.raises(HarlequinCopyError):
            write_file(data, tmp_path / "out.orc", "orc", {"bloom_filter_columns": "a"})


class TestFeatherOptions:
    @pytest.mark.parametrize("codec", ["uncompressed", "lz4", "zstd"])
    def test_compression(self, data: pa.Table, tmp_path: Path, codec: str) -> None:
        path = tmp_path / "out.feather"
        write_file(data, path, "feather", {"compression": codec})
        assert pf.read_table(str(path)).to_pydict() == data.to_pydict()

    def test_compression_level_and_chunksize(
        self, data: pa.Table, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.feather"
        write_file(
            data,
            path,
            "feather",
            {"compression": "zstd", "compression_level": "3", "chunksize": "1"},
        )
        assert pf.read_table(str(path)).to_pydict() == data.to_pydict()

    @pytest.mark.parametrize("version", ["1", "2"])
    def test_version(self, data: pa.Table, tmp_path: Path, version: str) -> None:
        path = tmp_path / "out.feather"
        write_file(data, path, "feather", {"version": int(version)})
        assert pf.read_table(str(path)).to_pydict() == data.to_pydict()

    def test_a_number_that_is_not_one_is_an_error(
        self, data: pa.Table, tmp_path: Path
    ) -> None:
        with pytest.raises(HarlequinCopyError):
            write_file(
                data, tmp_path / "out.feather", "feather", {"compression_level": "high"}
            )


class TestDeclaredOptions:
    """Whatever the copy dialog can send, the writer has to accept.

    The dialog renders every option a format declares and passes all of them,
    touched or not, so an option the writer has no name for is a silent no-op
    -- which is exactly how the JSON timestamp format did nothing at all.
    """

    @pytest.mark.parametrize("fmt", HARLEQUIN_COPY_FORMATS, ids=lambda f: str(f.name))
    def test_the_declared_defaults_are_accepted(
        self, data: pa.Table, tmp_path: Path, fmt: HarlequinCopyFormat
    ) -> None:
        """This also covers the numeric options arriving as strings.

        ORC and Feather declare theirs as text, so `"1024"` is what the dialog
        sends and what the writer has to turn into an int. Passing the declared
        defaults rather than invented values keeps this from failing on a
        pyarrow that has an opinion about, say, block sizes being a multiple of
        something.
        """
        # `default` is declared on the concrete option types, not the ABC.
        defaults: dict[str, Any] = {
            option.name: getattr(option, "default", None) for option in fmt.options
        }
        path = tmp_path / f"out.{fmt.name}"
        write_file(data, path, fmt.name, defaults)
        assert path.stat().st_size > 0

    @pytest.mark.parametrize(
        ("fmt", "option"),
        [
            (fmt, option)
            for fmt in HARLEQUIN_COPY_FORMATS
            for option in fmt.options
            if isinstance(option, SelectOption)
        ],
        ids=lambda p: str(getattr(p, "name", p)),
    )
    def test_every_choice_a_select_offers_is_accepted(
        self,
        data: pa.Table,
        tmp_path: Path,
        fmt: HarlequinCopyFormat,
        option: SelectOption,
    ) -> None:
        for choice in option.choices:
            value = choice if isinstance(choice, str) else choice[1]
            path = tmp_path / f"out-{value}.{fmt.name}"
            write_file(data, path, fmt.name, {option.name: value})
            assert path.stat().st_size > 0


class TestColumnNames:
    def test_duplicate_names_are_made_unique(self) -> None:
        """`select 1 as a, 2 as a` is legal SQL and a legal Arrow table. It is
        not something duckdb will export, or that a csv header can say."""
        data = pa.Table.from_arrays(
            [pa.array([1]), pa.array([2]), pa.array([3])], names=["a", "a", "a"]
        )
        assert to_bytes(data, "csv") == b"a,a0,a01\n1,2,3\n"

    @pytest.mark.parametrize(
        "names",
        [
            ["a", "a"],
            ["a", "a", "a"],
            ["a", "a0", "a"],
            ["b", "a", "a", "b"],
            ["a_0", "a", "a"],
        ],
        ids=lambda n: ",".join(n),
    )
    def test_it_de_duplicates_exactly_as_the_backend_does(
        self, names: list[str]
    ) -> None:
        """Two things de-duplicate, and they must not disagree.

        The app exports `source_data`, which still holds the duplicates, so
        `write_file()` resolves them. A headless caller exports rows that have
        already been through `create_backend()`, which resolved them first. The
        same query has to produce the same header either way.

        Asserted against what the backend actually does rather than against a
        copy of its algorithm, so this fails if upstream changes the scheme --
        at which point Harlequin follows it, rather than forking.
        """
        table = pa.Table.from_arrays([pa.array([1]) for _ in names], names=names)
        assert (
            _deduplicate_column_names(table).column_names
            == create_backend(table).data.column_names
        )

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
