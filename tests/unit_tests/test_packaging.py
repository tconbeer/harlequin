"""The hsql metapackage ships with Harlequin, so its version tracks Harlequin's.

`hsql` is a distribution that contains nothing but a dependency on `harlequin`
and the `hsql` console script Harlequin provides. One release goes out per
Harlequin release, carrying the same version number and pinning that release
exactly -- so `pip install hsql==2.9.0` and `pip install harlequin==2.9.0`
install the same two commands, and the number means one thing.

That is three values in two files that have to agree. `release.yml` sets all
three; this is what notices when something else didn't, on the release PR
rather than after the upload, since PyPI does not take a version back.
"""

from __future__ import annotations

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

# what the release workflow runs, and what to run by hand after editing either
# version. --frozen because the pin names a release that does not exist yet.
FIX = (
    "uv version --project packaging/hsql --frozen VERSION && "
    "uv add --project packaging/hsql --frozen harlequin==VERSION"
)


def _project(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return cast("dict[str, Any]", tomllib.load(f)["project"])


def test_hsql_metapackage_tracks_harlequin_version() -> None:
    harlequin = _project(HARLEQUIN_PYPROJECT)
    hsql = _project(HSQL_PYPROJECT)
    fix = FIX.replace("VERSION", harlequin["version"])

    assert hsql["version"] == harlequin["version"], (
        f"the hsql metapackage's version must match Harlequin's: {fix}"
    )

    (dependency,) = hsql["dependencies"]
    assert dependency == f"harlequin=={harlequin['version']}", (
        f"the hsql metapackage must pin this release of Harlequin exactly: {fix}"
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
