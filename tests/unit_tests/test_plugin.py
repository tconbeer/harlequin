"""The marketplace entry, and the plugin it makes of the skill directory.

`hsql --skill -o …` is the install that needs no network; this is the one that
needs no hsql. `.claude-plugin/marketplace.json` at the repo root names
`src/harlequin/hsql/skill` as a plugin, and the `.claude-plugin/plugin.json`
inside that directory is what makes it one -- no second repo, no copied file,
no build step.

What that costs is three manifests that have to go on agreeing, and none of
them fails loudly: a source path left behind by a move, or a version left
behind by a release, is a broken install for everyone who has already added
the marketplace.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from harlequin.hsql.modes import skill as skill_mode

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_PATH = skill_mode.SKILL_DIR / ".claude-plugin" / "plugin.json"

BUMP_SCRIPT_PATH = REPO_ROOT / "scripts" / "bump_plugin_version.py"

PACKAGED_PLUGIN_MANIFEST = "src/harlequin/hsql/skill/.claude-plugin/plugin.json"
"""The manifest's path from the repo root, as the build targets spell it."""


def skill_name() -> str:
    """The name in `SKILL.md`'s frontmatter, which is the skill's own."""
    match = re.match(
        r"^---\n(.*?)\n---\n", skill_mode.text().decode("utf-8"), re.DOTALL
    )
    assert match is not None
    name: str = yaml.safe_load(match.group(1))["name"]
    return name


@pytest.fixture(scope="module")
def marketplace() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="module")
def plugin() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(PLUGIN_PATH.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="module")
def bumper() -> ModuleType:
    """The release script, loaded from `scripts/`, which is not a package."""
    spec = importlib.util.spec_from_file_location(
        "bump_plugin_version", BUMP_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def entry(marketplace: dict[str, Any]) -> dict[str, Any]:
    """The one plugin the marketplace offers."""
    plugins = marketplace["plugins"]
    assert len(plugins) == 1
    offered: dict[str, Any] = plugins[0]
    return offered


# --- the manifests -----------------------------------------------------------


def test_the_marketplace_entry_names_a_source_that_exists(
    entry: dict[str, Any],
) -> None:
    """The source is a path inside this repo, which is what saves a second one."""
    source = (REPO_ROOT / entry["source"]).resolve()
    assert source == skill_mode.SKILL_DIR
    assert (source / skill_mode.FILENAME).is_file()


def test_the_plugin_manifest_sits_inside_the_source_directory() -> None:
    """`.claude-plugin/` beside `SKILL.md` is what makes the directory a plugin
    rather than a folder the marketplace points at."""
    assert PLUGIN_PATH.parent.parent == skill_mode.SKILL_DIR
    assert PLUGIN_PATH.is_file()


def test_one_name_names_the_plugin_everywhere(
    entry: dict[str, Any], plugin: dict[str, Any]
) -> None:
    """`/plugin install hsql@harlequin` is the entry's name and the manifest's,
    and the skill it installs answers to the same one."""
    assert entry["name"] == plugin["name"] == skill_name() == "hsql"


def test_the_plugin_version_is_the_version_it_ships_with(
    plugin: dict[str, Any],
) -> None:
    """The plugin is the skill for one release of hsql, so it carries that
    release's number -- and `release.yml` is what keeps this true."""
    assert plugin["version"] == version("harlequin")


def test_a_release_bumps_the_version_and_rewrites_nothing_else(
    bumper: ModuleType, tmp_path: Path
) -> None:
    """A manifest the release script would reformat is one nobody can read a
    version change out of."""
    assert bumper.PLUGIN_PATH == PLUGIN_PATH
    committed = PLUGIN_PATH.read_bytes()
    manifest = tmp_path / "plugin.json"
    manifest.write_bytes(committed)
    bumper.write_version("9.9.9", manifest)
    assert manifest.read_bytes() == committed.replace(
        f'"version": "{version("harlequin")}"'.encode(), b'"version": "9.9.9"'
    )


def test_the_plugin_manifest_ships_in_the_wheel_and_the_sdist() -> None:
    """hatchling skips dot-directories, so the manifest is included by name in
    both targets. Losing either is silent: the build succeeds without it."""
    targets = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["hatch"]["build"]["targets"]
    for target in ("wheel", "sdist"):
        assert PACKAGED_PLUGIN_MANIFEST in targets[target]["force-include"]


# --- what the plugin tooling makes of them -----------------------------------


@pytest.mark.skipif(
    shutil.which("claude") is None, reason="the claude CLI is what reads these"
)
@pytest.mark.parametrize("target", [".", "src/harlequin/hsql/skill"])
def test_claude_plugin_validate_passes(target: str, tmp_path: Path) -> None:
    """The marketplace and the plugin, checked by the tool that installs them.

    `--strict` because a warning here -- an unrecognized field, or metadata
    that is missing -- is a manifest some of the tooling reads differently.
    """
    proc = subprocess.run(
        ["claude", "plugin", "validate", target, "--strict"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
