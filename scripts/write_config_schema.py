"""Write the base JSON Schema for a config file into the package.

`hsql --config schema` generates a schema for the machine it runs on, adapters
and all. The file this writes is the other one: the same document built for no
adapters, so it describes the config format rather than an installation, and it
is what harlequin.sh publishes at the `$id` it carries.

Usage:
    uv run python scripts/write_config_schema.py

`tests/unit_tests/test_config_schema.py` is what fails when the file and the
generator disagree, which is any change to the config model, to hsql's own
options, or to what either describes.
"""

from __future__ import annotations

import json
from pathlib import Path

from harlequin.config_schema import build_schema
from harlequin.hsql.cli import bare_command

BASE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "harlequin"
    / "schemas"
    / "config-v1.json"
)


def main() -> None:
    BASE_SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = build_schema(bare_command().params, adapters=None)
    BASE_SCHEMA_PATH.write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {BASE_SCHEMA_PATH}")


if __name__ == "__main__":
    main()
