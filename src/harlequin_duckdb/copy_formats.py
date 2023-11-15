from harlequin.options import FlagOption, HarlequinCopyFormat, SelectOption, TextOption

csv = HarlequinCopyFormat(
    name="csv",
    label="CSV",
    extensions=(".csv", ".tsv"),
    options=[
        FlagOption(
            name="header",
            description="Switch on to include column name headers.",
            label="Header",
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

DUCKDB_COPY_FORMATS = [csv, parquet, json]
