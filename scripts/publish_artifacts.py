"""Stage the files harlequin.sh vendors, and the manifest that dates them.

Three artifacts are generated in this repo and read on the site: the base
config schema, whose `$id` is a URL there; the generated CLI reference, which
is a docs page; and the skill, which the site publishes beside the two install
lines that do not need it. The site vendors them rather than fetching them at
build time, so its build stays hermetic and a stale page is the worst this
pipeline can do to it.

Usage:
    uv run python scripts/publish_artifacts.py [--output DIRECTORY]

Copies rather than generates: the committed files are what the wheel ships and
what `test_cli_reference.py` and `test_config_schema.py` pin, and a publish
that regenerated them could put something on the site that is in no release.
The output is a pure function of the checkout -- no timestamps, no host -- so
running it twice writes the same bytes and the vendoring PR's diff is only what
actually changed.

`.github/workflows/publish-artifacts.yml` runs this and opens that PR.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any, NamedTuple

from harlequin.hsql.modes import skill as skill_mode

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OUTPUT = REPO_ROOT / "dist" / "artifacts"

MANIFEST_NAME = "manifest.json"
"""What records the version the others came from, so a reader can tell when
the page they are on is behind the release they have installed."""


class Artifact(NamedTuple):
    """One file this repo publishes, and the name it is published under."""

    source: Path
    name: str


def artifacts() -> list[Artifact]:
    """Every file the site vendors, in the layout it vendors them in.

    The references come from the skill directory rather than a list, because a
    `SKILL.md` whose pointers dangle is the one way this can publish a skill
    that is worse than no skill -- the same reason `--skill -o` copies them.
    """
    staged = [
        Artifact(
            REPO_ROOT / "src" / "harlequin" / "schemas" / "config-v1.json",
            "config-v1.json",
        ),
        Artifact(
            REPO_ROOT / "docs" / "generated" / "hsql-reference.md", "hsql-reference.md"
        ),
        Artifact(skill_mode.SKILL_DIR / skill_mode.FILENAME, skill_mode.FILENAME),
    ]
    staged.extend(
        Artifact(path, f"{skill_mode.REFERENCES}/{path.name}")
        for path in skill_mode.reference_paths()
    )
    return staged


def build_manifest(staged: list[Artifact]) -> dict[str, Any]:
    """What was published, from where, and out of which release."""
    return {
        "version": version("harlequin"),
        "generated_by": "scripts/publish_artifacts.py",
        "files": [
            {
                "path": artifact.name,
                "source": artifact.source.relative_to(REPO_ROOT).as_posix(),
                "bytes": len(artifact.source.read_bytes()),
                "sha256": hashlib.sha256(artifact.source.read_bytes()).hexdigest(),
            }
            for artifact in sorted(staged, key=lambda artifact: artifact.name)
        ],
    }


def stage(directory: Path) -> list[Path]:
    """Write every artifact and the manifest into `directory`, and say which.

    Byte-for-byte copies: a line ending changed on the way through is a diff on
    every publish and a file the site serves that no release contains.
    """
    staged = artifacts()
    missing = [artifact.source for artifact in staged if not artifact.source.is_file()]
    if missing:
        raise FileNotFoundError(
            "cannot publish what is not here: "
            + ", ".join(path.relative_to(REPO_ROOT).as_posix() for path in missing)
        )
    written = []
    for artifact in staged:
        destination = directory / artifact.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(artifact.source.read_bytes())
        written.append(destination)
    manifest = directory / MANIFEST_NAME
    manifest.write_text(
        json.dumps(build_manifest(staged), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    written.append(manifest)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="the directory to stage the artifacts in",
    )
    directory = parser.parse_args().output
    for path in stage(directory):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
