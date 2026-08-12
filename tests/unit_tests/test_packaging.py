"""The hsql metapackage ships with Harlequin, so its version tracks Harlequin's.

`hsql` is a distribution that contains nothing but a dependency on `harlequin`
and the `hsql` console script Harlequin provides. One release goes out per
Harlequin release, carrying the same version number and pinning that release
exactly -- so `pip install hsql==2.9.0` and `pip install harlequin==2.9.0`
install the same two commands, and the number means one thing.

That is three values in two files that have to agree, kept that way by
`scripts/sync_hsql_version.py`. This is what notices when they don't, on the
release PR rather than after the upload: PyPI does not take a version back.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, cast

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
HARLEQUIN_PYPROJECT = REPO_ROOT / "pyproject.toml"
HSQL_PYPROJECT = REPO_ROOT / "packaging" / "hsql" / "pyproject.toml"


def _project(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return cast("dict[str, Any]", tomllib.load(f)["project"])


def test_hsql_metapackage_tracks_harlequin_version() -> None:
    harlequin = _project(HARLEQUIN_PYPROJECT)
    hsql = _project(HSQL_PYPROJECT)

    assert hsql["version"] == harlequin["version"], (
        "the hsql metapackage's version must match Harlequin's; "
        "run `python scripts/sync_hsql_version.py`"
    )

    (dependency,) = hsql["dependencies"]
    assert dependency == f"harlequin=={harlequin['version']}", (
        "the hsql metapackage must pin this release of Harlequin exactly; "
        "run `python scripts/sync_hsql_version.py`"
    )


def test_hsql_metapackage_declares_harlequins_console_script() -> None:
    """Both distributions must name the same entry point.

    Two distributions providing one script name is fine only while the scripts
    are byte-identical -- point this at anything else and whichever installs
    second silently wins.
    """
    harlequin = _project(HARLEQUIN_PYPROJECT)
    hsql = _project(HSQL_PYPROJECT)

    assert hsql["scripts"] == {"hsql": harlequin["scripts"]["hsql"]}


def test_sync_script_finds_what_it_edits() -> None:
    """The script the release workflow runs must still fit this file.

    It reaches for `[project]`, a version and a one-element `harlequin==` pin,
    and reports anything else as an error rather than writing a release with a
    dependency it did not understand. Run against an already-synced tree it
    must change nothing and exit 0; the assertions above are what prove the
    tree is synced.
    """
    before = HSQL_PYPROJECT.read_text()
    try:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "sync_hsql_version.py")],
            capture_output=True,
            text=True,
        )
        after = HSQL_PYPROJECT.read_text()
    finally:
        HSQL_PYPROJECT.write_text(before)

    assert result.returncode == 0, result.stderr
    assert after == before
