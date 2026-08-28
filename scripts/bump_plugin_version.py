"""Set the plugin manifest's version, the way a release does.

The plugin is the skill for one release of hsql, so `plugin.json` carries that
release's number -- and it is the only version `uv version` cannot reach. So
`release.yml` runs this beside the two it does.

Usage:
    python scripts/bump_plugin_version.py 2.12.0

`tests/unit_tests/test_plugin.py` is what fails when the committed manifest and
this disagree, whether that is a version a release left behind or a hand edit
this would reformat.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "harlequin"
    / "hsql"
    / "skill"
    / ".claude-plugin"
    / "plugin.json"
)


def write_version(version: str, path: Path = PLUGIN_PATH) -> None:
    """Rewrite the manifest with `version`, and nothing else changed.

    Key order survives the round trip, so a release's diff is the one line.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["version"] = version
    path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/bump_plugin_version.py VERSION")
    write_version(sys.argv[1])
    print(f"wrote {PLUGIN_PATH}")


if __name__ == "__main__":
    main()
