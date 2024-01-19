from __future__ import annotations

import pytest
from harlequin.components.data_catalog import S3Tree


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("my-bucket", (None, "my-bucket", "")),
        ("my-bucket/my-prefix", (None, "my-bucket", "my-prefix")),
        ("s3://my-bucket", (None, "my-bucket", "")),
        ("s3://my-bucket/my-prefix", (None, "my-bucket", "my-prefix")),
        (
            "https://my-storage.com/my-bucket/",
            ("https://my-storage.com", "my-bucket", ""),
        ),
        (
            "https://my-storage.com/my-bucket/my-prefix",
            ("https://my-storage.com", "my-bucket", "my-prefix"),
        ),
        (
            "https://storage.my-api.com/my-bucket/",
            ("https://storage.my-api.com", "my-bucket", ""),
        ),
        ("https://my-bucket.s3.amazonaws.com", (None, "my-bucket", "")),
        (
            "https://my-bucket.s3.amazonaws.com/my-prefix",
            (None, "my-bucket", "my-prefix"),
        ),
        (
            "https://my-bucket.storage.googleapis.com",
            ("https://storage.googleapis.com", "my-bucket", ""),
        ),
        (
            "https://my-bucket.storage.googleapis.com/my-prefix",
            ("https://storage.googleapis.com", "my-bucket", "my-prefix"),
        ),
    ],
)
def test_s3_uri_parsing(uri: str, expected: tuple[str | None, str, str]) -> None:
    actual = S3Tree._parse_s3_uri(uri)
    assert actual == expected
