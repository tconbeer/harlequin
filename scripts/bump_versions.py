"""Set the versions `uv version` cannot reach, the way a release does.

Two of them, and each is a literal for the same reason: the thing that reads it
cannot afford to ask the package metadata. The plugin manifest is JSON that a
harness reads without Python at all, and `harlequin.hsql.protocol` is on the
warm-session client's path, where `importlib.metadata.version()` costs ~43ms
against a whole invocation of about twenty. So `release.yml` runs this beside
the two versions `uv version` does bump.

Usage:
    python scripts/bump_versions.py 2.12.0

`tests/unit_tests/test_plugin.py` and `tests/unit_tests/test_hsql_session.py`
are what fail when a committed literal and the installed version disagree,
whether that is a version a release left behind or a hand edit.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "harlequin"

PLUGIN_PATH = SOURCE_ROOT / "hsql" / "skill" / ".claude-plugin" / "plugin.json"

PROTOCOL_PATH = SOURCE_ROOT / "hsql" / "protocol.py"

_PROTOCOL_VERSION = re.compile(r'^VERSION = "[^"]*"(?=\r?$)', re.MULTILINE)
"""The one line to rewrite, matched without consuming the newline before it.

A lookahead rather than `$`, because the file is read untranslated (see
`write_protocol_version`): with `\\r\\n` endings, `$` sits after the `\\r`, so a
pattern ending in `"` would never match one.
"""


def write_plugin_version(version: str, path: Path = PLUGIN_PATH) -> None:
    """Rewrite the manifest with `version`, and nothing else changed.

    Key order survives the round trip, so a release's diff is the one line.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["version"] = version
    path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def write_protocol_version(version: str, path: Path = PROTOCOL_PATH) -> None:
    """Rewrite the session protocol's version, and nothing else.

    Read and written with `newline=""`, so the file's own line endings survive:
    this edits one line of a source file, and a checkout with CRLF endings --
    which is every Windows one, since `.gitattributes` pins only the artifacts
    whose bytes are a contract -- would otherwise come back with every other
    line rewritten too. `open()` rather than `Path.read_text()`, which only
    takes `newline` on 3.13 and up.
    """
    with path.open("r", encoding="utf-8", newline="") as source_file:
        source = source_file.read()
    rewritten, replacements = _PROTOCOL_VERSION.subn(f'VERSION = "{version}"', source)
    if replacements != 1:
        raise SystemExit(f"{path} declares VERSION {replacements} times, not once")
    with path.open("w", encoding="utf-8", newline="") as target_file:
        target_file.write(rewritten)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/bump_versions.py VERSION")
    write_plugin_version(sys.argv[1])
    write_protocol_version(sys.argv[1])
    print(f"wrote {PLUGIN_PATH} and {PROTOCOL_PATH}")


if __name__ == "__main__":
    main()
