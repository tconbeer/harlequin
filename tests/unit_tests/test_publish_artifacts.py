"""The files this repo publishes to harlequin.sh, and the manifest that dates them.

Three artifacts are generated here and read there, and nothing at run time
reads any of them, so the only thing that notices a broken publish is a reader
on the site. What can go wrong is narrow and worth pinning: an artifact that
stops being copied, a skill published without the references it points at, a
manifest that names a version nobody can install, and a byte changed on the way
through.

The workflow that carries them is checked here too -- by name, because a script
renamed out from under it fails at release time, once a year, in the other repo.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from importlib.metadata import version
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harlequin.config_schema import SCHEMA_ID
from harlequin.hsql.modes import skill as skill_mode

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_NAME = "scripts/publish_artifacts.py"
SCRIPT_PATH = REPO_ROOT / "scripts" / "publish_artifacts.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "publish-artifacts.yml"
PUBLISH_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "publish.yml"


@pytest.fixture(scope="module")
def publisher() -> ModuleType:
    """The script, loaded from `scripts/`, which is not an importable package."""
    spec = importlib.util.spec_from_file_location("publish_artifacts", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def staged(publisher: ModuleType, tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("artifacts")
    publisher.stage(directory)
    return directory


@pytest.fixture(scope="module")
def manifest(staged: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (staged / "manifest.json").read_text(encoding="utf-8")
    )
    return data


def _relative_paths(directory: Path) -> set[str]:
    return {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    }


# --- what gets published -----------------------------------------------------


def test_the_three_artifacts_and_the_manifest_are_what_ships(staged: Path) -> None:
    """Named here rather than derived, so dropping one is a failing test and
    not a page that silently stops updating."""
    references = {f"references/{path.name}" for path in skill_mode.reference_paths()}
    assert (
        _relative_paths(staged)
        == {
            "config-v1.json",
            "hsql-reference.md",
            "SKILL.md",
            "manifest.json",
        }
        | references
    )


def test_every_artifact_is_the_committed_file_byte_for_byte(
    publisher: ModuleType, staged: Path
) -> None:
    """It publishes what the wheel ships. A line ending changed on the way
    through is a diff on every release and a file that is in no release."""
    for artifact in publisher.artifacts():
        assert (staged / artifact.name).read_bytes() == artifact.source.read_bytes()


def test_the_skill_is_published_with_the_references_it_points_at(
    staged: Path,
) -> None:
    """A `SKILL.md` whose pointers dangle is worse than one that never had
    them: the agent reading it cannot tell a missing reference from one that
    does not exist. So a new reference file is published by adding it."""
    assert (staged / skill_mode.FILENAME).read_bytes() == skill_mode.text()
    published = skill_mode.reference_paths()
    assert published
    for reference in published:
        assert (staged / "references" / reference.name).read_bytes() == (
            reference.read_bytes()
        )


def test_the_published_schema_is_the_one_that_names_the_site(staged: Path) -> None:
    """The whole reason this pipeline exists: `$id` has pointed at a URL on the
    site since 2.10, and this is the file that has to be at it."""
    schema = json.loads((staged / "config-v1.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == SCHEMA_ID
    assert SCHEMA_ID.startswith("https://harlequin.sh/")


def test_a_missing_source_stops_the_publish(
    publisher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Half an upload is the one outcome worse than a stale page."""
    missing = publisher.Artifact(REPO_ROOT / "docs" / "nope.md", "nope.md")
    monkeypatch.setattr(publisher, "artifacts", lambda: [missing])
    with pytest.raises(FileNotFoundError, match="docs/nope.md"):
        publisher.stage(tmp_path)
    assert not list(tmp_path.iterdir())


# --- the manifest ------------------------------------------------------------


def test_the_manifest_names_the_release_the_artifacts_came_from(
    manifest: dict[str, Any],
) -> None:
    """It is what the site's reference page prints as its generated-from line,
    so a reader can tell whether the page is behind the hsql they have."""
    assert manifest["version"] == version("harlequin")


def test_the_manifest_describes_every_file_beside_it(
    manifest: dict[str, Any], staged: Path
) -> None:
    described = {entry["path"] for entry in manifest["files"]}
    assert described == _relative_paths(staged) - {"manifest.json"}
    for entry in manifest["files"]:
        content = (staged / entry["path"]).read_bytes()
        assert entry["bytes"] == len(content)
        assert entry["sha256"] == hashlib.sha256(content).hexdigest()
        assert (REPO_ROOT / entry["source"]).is_file()


def test_publishing_twice_writes_the_same_bytes(
    publisher: ModuleType, staged: Path, tmp_path: Path
) -> None:
    """No timestamp, no hostname, no ordering by directory: a re-run's PR is
    empty when nothing changed, so a diff on the site is a real change."""
    publisher.stage(tmp_path)
    assert _relative_paths(tmp_path) == _relative_paths(staged)
    for name in _relative_paths(tmp_path):
        assert (tmp_path / name).read_bytes() == (staged / name).read_bytes()


# --- the workflow that carries them ------------------------------------------


def test_the_workflow_runs_this_script() -> None:
    """Renaming the script is a release-time failure in another repo otherwise."""
    assert SCRIPT_NAME in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_the_generators_the_workflow_checks_are_the_ones_that_write_artifacts(
    publisher: ModuleType,
) -> None:
    """It refuses to publish a committed artifact its generator disagrees with,
    which only works while it names both generators."""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    for generator in ("write_config_schema.py", "write_cli_reference.py"):
        assert f"scripts/{generator}" in workflow


def test_a_release_publishes_the_artifacts(publisher: ModuleType) -> None:
    """The manifest names a version, so it goes out when that version does."""
    publish = PUBLISH_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "./.github/workflows/publish-artifacts.yml" in publish
