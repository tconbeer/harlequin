from __future__ import annotations

from harlequin.options import (
    FlagOption,
    HarlequinCopyFormat,
    SelectOption,
    TextOption,
)


def _validate_int(raw: str) -> tuple[bool, str | None]:
    try:
        int(raw)
    except ValueError:
        return False, "Must be an integer."
    else:
        return True, None


def _validate_int_or_empty(raw: str) -> tuple[bool, str | None]:
    if raw == "":
        return True, None
    else:
        return _validate_int(raw)


def _validate_float(raw: str) -> tuple[bool, str | None]:
    try:
        float(raw)
    except ValueError:
        return False, "Must be a floating point number."
    else:
        return True, None


csv = HarlequinCopyFormat(
    name="csv",
    label="CSV",
    extensions=(".csv", ".tsv"),
    options=[
        FlagOption(
            name="header",
            description="Switch on to include column name headers.",
            label="Header",
            default=True,
        ),
        TextOption(
            name="sep",
            description="The separator (or delimeter) between cols in each row.",
            label="Separator",
            default=",",
        ),
        SelectOption(
            name="compression",
            description=(
                "The compression type for the file. By default this will be detected "
                "automatically from the file extension (e.g. file.csv.gz will use "
                "gzip, file.csv will use no compression)."
            ),
            default="auto",
            choices=[
                ("Auto", "auto"),
                ("gzip", "gzip"),
                ("zstd", "zstd"),
                ("No compression", "none"),
            ],
        ),
        FlagOption(
            name="quoting",
            description="Switch on to always quote all strings.",
            label="Force Quote",
        ),
        TextOption(
            name="date_format",
            description="Specifies the date format to use when writing dates.",
            label="Date Format",
            default="",
            placeholder="%Y-%m-%d",
        ),
        TextOption(
            name="timestamp_format",
            description="Specifies the date format to use when writing timestamps.",
            label="Timestamp Format",
            default="",
            placeholder="%c",
        ),
        TextOption(
            name="quotechar",
            description="The quoting character to be used when a data value is quoted.",
            label="Quote Char",
            default='"',
        ),
        TextOption(
            name="escapechar",
            description=(
                "The character that should appear before a character that matches the "
                "quote value."
            ),
            label="Escape Char",
            default='"',
        ),
        TextOption(
            name="na_rep",
            description="The string that is written to represent a NULL value.",
            label="Null String",
            default="",
        ),
        TextOption(
            name="encoding",
            description="Only UTF8 is currently supported by DuckDB.",
            label="Encoding",
            default="UTF8",
        ),
    ],
)


parquet = HarlequinCopyFormat(
    name="parquet",
    label="Parquet",
    extensions=(".parquet", ".pq"),
    options=[
        SelectOption(
            name="compression",
            description=(
                "The compression format to use (uncompressed, snappy, gzip or zstd). "
                "Default snappy."
            ),
            choices=[
                ("Snappy", "snappy"),
                ("gzip", "gzip"),
                ("zstd", "zstd"),
                ("Uncompressed", "uncompressed"),
            ],
            default="snappy",
        )
    ],
)


json = HarlequinCopyFormat(
    name="json",
    label="JSON",
    extensions=(".json", ".js", ".ndjson"),
    options=[
        FlagOption(
            name="array",
            description=(
                "Whether to write a JSON array. If true, a JSON array of records is "
                "written, if false, newline-delimited JSON is written."
            ),
        ),
        SelectOption(
            name="compression",
            description=(
                "The compression type for the file. By default this will be detected "
                "automatically from the file extension (e.g. file.json.gz will use "
                "gzip, file.json will use no compression)."
            ),
            choices=[
                ("Auto", "auto"),
                ("gzip", "gzip"),
                ("zstd", "zstd"),
                ("Uncompressed", "uncompressed"),
            ],
            default="auto",
        ),
        TextOption(
            name="date_format",
            description="Specifies the date format to use when writing dates.",
            label="Date Format",
            default="",
            placeholder="%Y-%m-%d",
        ),
        TextOption(
            name="timestamp_format",
            description="Specifies the date format to use when writing timestamps.",
            label="Timestamp Format",
            default="",
            placeholder="%c",
        ),
    ],
)

orc = HarlequinCopyFormat(
    name="orc",
    label="ORC",
    extensions=(".orc",),
    options=[
        SelectOption(
            name="file_version",
            description=(
                "Determine which ORC file version to use. Hive 0.11 / ORC v0 is the "
                "older version while Hive 0.12 / ORC v1 is the newer one."
            ),
            label="File Version",
            choices=[
                ("0.11", "0.11"),
                ("0.12", "0.12"),
            ],
            default="0.12",
        ),
        TextOption(
            name="batch_size",
            description="Number of rows the ORC writer writes at a time.",
            label="Batch Size",
            default="1024",
            validator=_validate_int,
        ),
        TextOption(
            name="stripe_size",
            description="Size of each ORC stripe in bytes.",
            label="Stripe Size",
            default=str(64 * 1024 * 1024),
            validator=_validate_int,
        ),
        SelectOption(
            name="compression",
            label="Compression",
            description="The compression codec.",
            choices=[
                ("Uncompressed", "UNCOMPRESSED"),
                ("Snappy", "SNAPPY"),
                ("zlib", "ZLIB"),
                ("LZ4", "LZ4"),
                ("zstd", "zstd"),
            ],
            default="UNCOMPRESSED",
        ),
        TextOption(
            name="compression_block_size",
            label="Compression Block Size",
            description="Size of each compression block in bytes.",
            default=str(64 * 1024),
            validator=_validate_int,
        ),
        SelectOption(
            name="compression_strategy",
            label="Compression Strategy",
            description="The compression strategy i.e. speed vs size reduction.",
            choices=[
                ("Speed", "SPEED"),
                ("Compression", "COMPRESSION"),
            ],
            default="SPEED",
        ),
        TextOption(
            name="row_index_stride",
            description=(
                "The row index stride i.e. the number of rows per an entry in the "
                "row index."
            ),
            label="Row Index Stride",
            default="10000",
            validator=_validate_int,
        ),
        TextOption(
            name="padding_tolerance",
            label="Padding Tolerance",
            description="The padding tolerance.",
            default="0.0",
            validator=_validate_float,
        ),
        TextOption(
            name="dictionary_key_size_threshold",
            label="Dict Key Size Threshold",
            description=(
                "The dictionary key size threshold. 0 to disable dictionary encoding. "
                "1 to always enable dictionary encoding."
            ),
            default="0.0",
            validator=_validate_float,
        ),
        TextOption(
            name="bloom_filter_columns",
            label="Bloom Filter Columns",
            description="Columns that use the bloom filter. Separate with a comma",
        ),
        TextOption(
            name="bloom_filter_fpp",
            label="Bloom Filter False-Positive",
            description=("Upper limit of the false-positive rate of the bloom filter."),
            default="0.05",
            validator=_validate_float,
        ),
    ],
)

feather = HarlequinCopyFormat(
    name="feather",
    label="Feather",
    extensions=(".feather",),
    options=[
        SelectOption(
            name="compression",
            label="Compression",
            description="The compression codec.",
            choices=[
                ("Uncompressed", "uncompressed"),
                ("LZ4", "lz4"),
                ("zstd", "zstd"),
            ],
            default="uncompressed",
        ),
        TextOption(
            name="compression_level",
            label="Compression Level",
            description=(
                "Use a compression level particular to the chosen compressor. "
                "If None use the default compression level."
            ),
            default="",
            validator=_validate_int_or_empty,
        ),
        TextOption(
            name="chunksize",
            label="Chunk Size",
            description=(
                "For V2 files, the internal maximum size of Arrow RecordBatch chunks "
                "when writing the Arrow IPC file format. None means use the default, "
                "which is currently 64K."
            ),
            default="",
            validator=_validate_int_or_empty,
        ),
        SelectOption(
            name="version",
            description=(
                "Feather file version. Version 2 is the current. Version 1 is the "
                "more limited legacy format."
            ),
            label="File Version",
            choices=[
                ("2", "2"),
                ("1", "1"),
            ],
            default="2",
        ),
    ],
)

HARLEQUIN_COPY_FORMATS = [csv, parquet, json, orc, feather]
WINDOWS_COPY_FORMATS = [csv, parquet, json, feather]
