"""Set the hsql metapackage's version and pin from Harlequin's version.

`hsql` ships one release per Harlequin release, carrying the same number and
pinning that release exactly, so three values have to agree: Harlequin's
version, hsql's version, and hsql's `harlequin==` dependency. This is what
makes them agree, and `tests/unit_tests/test_packaging.py` is what fails when
they don't.

The release workflow runs it right after `uv version`, so the bump lands in
the same commit. Run it by hand after any manual edit to either version:

    uv run scripts/sync_hsql_version.py

It edits through tomlkit -- Harlequin's own config writer, and a dependency
already -- rather than rewriting the text, because packaging/hsql/pyproject.toml
is mostly comments explaining why each of these values is what it is, and those
are the point.
"""

from __future__ import annotations

from pathlib import Path

import tomlkit
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import Array, Table
from tomlkit.toml_document import TOMLDocument

REPO_ROOT = Path(__file__).resolve().parents[1]
HARLEQUIN_PYPROJECT = REPO_ROOT / "pyproject.toml"
HSQL_PYPROJECT = REPO_ROOT / "packaging" / "hsql" / "pyproject.toml"


def _project(path: Path, document: TOMLDocument) -> Table | OutOfOrderTableProxy:
    """The [project] table, however it is laid out.

    Harlequin's own pyproject splits `[project]` across the file -- the urls,
    the scripts and the entry points come after `[dependency-groups]` -- and
    tomlkit hands back a proxy over the pieces rather than a `Table` for that.
    Both read and write the same way.
    """
    project = document.get("project")
    if not isinstance(project, (Table, OutOfOrderTableProxy)):
        raise SystemExit(f"error: {path} has no [project] table")
    return project


def main() -> None:
    harlequin_pyproject = HARLEQUIN_PYPROJECT.read_text()
    version = str(
        _project(HARLEQUIN_PYPROJECT, tomlkit.parse(harlequin_pyproject))["version"]
    )

    original = HSQL_PYPROJECT.read_text()
    hsql = tomlkit.parse(original)
    project = _project(HSQL_PYPROJECT, hsql)

    project["version"] = version

    # the pin is edited in place: assigning a list would flatten the array onto
    # one line. hsql depends on Harlequin and nothing else, so anything else
    # here is a mistake to report rather than an entry to skip past.
    dependencies = project["dependencies"]
    if not isinstance(dependencies, Array) or len(dependencies) != 1:
        raise SystemExit(
            f"error: expected exactly one dependency in {HSQL_PYPROJECT}, "
            f"got {project['dependencies']}"
        )
    if not str(dependencies[0]).startswith("harlequin=="):
        raise SystemExit(
            f"error: {HSQL_PYPROJECT} pins {dependencies[0]}, not harlequin"
        )
    dependencies[0] = f"harlequin=={version}"

    updated = tomlkit.dumps(hsql)
    if updated == original:
        print(f"hsql is already {version}.")
        return

    HSQL_PYPROJECT.write_text(updated)
    print(f"Set hsql to {version}, pinning harlequin=={version}.")


if __name__ == "__main__":
    main()
