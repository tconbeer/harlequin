"""The Agent Skill hsql ships, and the properties that keep it loadable.

The skill is the first thing an agent reads about hsql, and unlike the CLI it
has no way to fail loudly: a frontmatter key the standard does not define makes
the file unloadable in half the harnesses that read it, and a body that has
grown into a manual still loads and still costs every session that triggers it.
Neither shows up in a test of what hsql does, so they are asserted here.

The flag guard in `test_documented_flags.py` covers the other half -- that every
command line the skill types is one hsql would accept.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml
from click.testing import CliRunner, Result

from harlequin.hsql.cli import build_cli
from harlequin.hsql.diagnostics import ExitCode
from harlequin.hsql.modes import skill as skill_mode

Hsql = Callable[..., Result]

PORTABLE_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
"""The six fields the Agent Skills standard defines.

Claude Code accepts extension keys on top of these; the claude.ai upload and the
Skills API reject an unknown key outright. A skill whose whole argument is that
it works in any harness does not get to use one.
"""

MAX_BODY_BYTES = 4096
MAX_BODY_LINES = 200
"""What the skill's text costs every session that triggers it, capped.

The references are not capped the same way: they are read on demand, by the
agent that needed one, and never enter a context that did not ask for them.
"""

REFERENCE_NAMES = {"config.md", "queries.md", "scripting.md", "troubleshooting.md"}


@pytest.fixture
def hsql(no_discovered_config: None) -> Hsql:
    runner = CliRunner()

    def _run(*args: str, **kwargs: Any) -> Result:
        argv = [str(arg) for arg in args]
        return runner.invoke(build_cli(argv), argv, catch_exceptions=False, **kwargs)

    return _run


def _split(text: str) -> tuple[dict[str, Any], str]:
    """One skill file as its frontmatter and its body."""
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert match is not None, "SKILL.md must open with a YAML frontmatter block"
    frontmatter = yaml.safe_load(match.group(1))
    assert isinstance(frontmatter, dict)
    return frontmatter, match.group(2)


@pytest.fixture
def frontmatter() -> dict[str, Any]:
    return _split(skill_mode.text().decode("utf-8"))[0]


@pytest.fixture
def body() -> str:
    return _split(skill_mode.text().decode("utf-8"))[1]


# --- the file, as a skill ----------------------------------------------------


def test_the_shipped_skill_has_lf_line_endings() -> None:
    """`--skill` copies these bytes out verbatim, so their line endings are part
    of what it promises, and a CRLF checkout is what takes them away: the same
    hsql would print different bytes on Windows, and the frontmatter would stop
    parsing. `.gitattributes` pins the directory to LF, and this is what fails,
    naming the cause, if that pin goes missing.
    """
    shipped = [
        skill_mode.SKILL_DIR / skill_mode.FILENAME,
        *skill_mode.reference_paths(),
    ]
    for path in shipped:
        assert b"\r" not in path.read_bytes(), (
            f"{path.name} has CRLF line endings; check the .gitattributes pin on "
            "src/harlequin/hsql/skill/"
        )


def test_the_frontmatter_uses_only_the_portable_fields(
    frontmatter: dict[str, Any],
) -> None:
    """What keeps the file loadable outside Claude Code."""
    unknown = sorted(set(frontmatter) - PORTABLE_FIELDS)
    assert not unknown, f"not in the Agent Skills standard: {unknown}"


def test_the_frontmatter_names_and_describes_the_skill(
    frontmatter: dict[str, Any],
) -> None:
    """The description is the only part always in context, and it is what
    decides whether the skill loads at all."""
    assert frontmatter["name"] == "hsql"
    assert frontmatter["description"].strip()


def test_the_description_names_the_contexts_as_well_as_the_capability(
    frontmatter: dict[str, Any],
) -> None:
    """The failure mode is under-triggering, so a description that says only
    what hsql is will not fire on the queries that should reach it."""
    description = frontmatter["description"].lower()
    for context in ("sql", "database", ".sql", "connection string", "export"):
        assert context in description, f"the description never mentions {context}"


def test_the_granted_tools_are_the_ones_that_only_read(
    frontmatter: dict[str, Any],
) -> None:
    """Deciding whether to run a query is the human's; deciding whether to read
    a catalog is not. A blanket `Bash(hsql *)` would pre-approve both."""
    granted = frontmatter["allowed-tools"]
    assert "Bash(hsql *)" not in granted
    for grant in granted:
        mode = grant.removeprefix("Bash(hsql ").split()[0].rstrip("*)")
        assert mode in {
            "--help",
            "--version",
            "--info",
            "--spec",
            "--catalog",
            "--catalog-search",
        }, f"{grant} pre-approves more than introspection"


def test_the_body_stays_a_skill_rather_than_a_manual(body: str) -> None:
    assert len(body.encode("utf-8")) <= MAX_BODY_BYTES
    assert body.count("\n") <= MAX_BODY_LINES


def test_the_body_points_at_every_reference_that_ships(body: str) -> None:
    """A pointer the shipped tree cannot satisfy is worse than no pointer: the
    agent reading it cannot tell a missing file from one that never existed."""
    shipped = {path.name for path in skill_mode.reference_paths()}
    assert shipped == REFERENCE_NAMES
    for name in shipped:
        assert name in body, f"SKILL.md never mentions references/{name}"


def test_every_reference_is_reachable_from_the_skill_directory() -> None:
    """`references/` is the name SKILL.md types, so it is the name it keeps."""
    for path in skill_mode.reference_paths():
        assert path.parent == skill_mode.SKILL_DIR / skill_mode.REFERENCES


# --- the mode ----------------------------------------------------------------


def test_skill_writes_the_packaged_file_verbatim(hsql: Hsql) -> None:
    """The bytes are the contract, applied to the one output that is not rows."""
    res = hsql("--skill")
    assert res.exit_code == ExitCode.OK
    assert res.stdout.encode("utf-8") == skill_mode.text()


def test_a_file_output_installs_the_whole_skill(hsql: Hsql, tmp_path: Path) -> None:
    """The documented one-liner, and it has to leave a skill rather than a file:
    `SKILL.md` alone would ship four pointers that resolve to nothing."""
    destination = tmp_path / "skills" / "hsql" / "SKILL.md"
    res = hsql("--skill", "-o", str(destination))
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert destination.read_bytes() == skill_mode.text()
    installed = {path.name for path in (destination.parent / "references").iterdir()}
    assert installed == REFERENCE_NAMES


def test_a_directory_output_installs_the_whole_skill(
    hsql: Hsql, tmp_path: Path
) -> None:
    """The other spelling of the same intent, and it lands in the same place."""
    destination = tmp_path / "skills" / "hsql"
    res = hsql("--skill", "-o", f"{destination}/")
    assert res.exit_code == ExitCode.OK
    assert (destination / "SKILL.md").read_bytes() == skill_mode.text()
    installed = {path.name for path in (destination / "references").iterdir()}
    assert installed == REFERENCE_NAMES


def test_the_install_names_every_file_it_wrote(hsql: Hsql, tmp_path: Path) -> None:
    """The caller named one path and got five files; the other four are the one
    thing about the run that stdout cannot carry."""
    res = hsql("--skill", "-o", f"{tmp_path}/hsql/")
    assert "SKILL.md" in res.stderr
    for name in REFERENCE_NAMES:
        assert f"references/{name}" in res.stderr


def test_writing_it_produces_the_same_bytes_as_printing_it(
    hsql: Hsql, tmp_path: Path
) -> None:
    destination = tmp_path / "SKILL.md"
    hsql("--skill", "-o", str(destination))
    assert destination.read_bytes() == hsql("--skill").stdout.encode("utf-8")


def test_format_none_writes_nothing(hsql: Hsql, tmp_path: Path) -> None:
    res = hsql("--skill", "--format", "none")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""


def test_a_format_it_cannot_honor_says_so(hsql: Hsql) -> None:
    """Silence would read as a format that was applied."""
    res = hsql("--skill", "--csv")
    assert res.exit_code == ExitCode.OK
    assert res.stdout.encode("utf-8") == skill_mode.text()
    assert "had no effect" in res.stderr


def test_skill_does_not_run_sql(hsql: Hsql) -> None:
    res = hsql("--skill", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "--skill does not run SQL" in res.stderr


def test_skill_is_one_mode(hsql: Hsql) -> None:
    res = hsql("--skill", "--info")
    assert res.exit_code == ExitCode.USAGE
    assert "two modes" in res.stderr


def test_skill_imports_no_adapter_and_reads_no_config(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """Printing a packaged file should cost what printing a packaged file costs.

    It is the one mode with nothing to look up: no adapter to ask about its
    options, no config file to resolve a profile from, no database.
    """
    proc = run_python(
        "import sys\n"
        "sys.argv = ['hsql', '--skill']\n"
        "from harlequin.hsql import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules "
        "if m.startswith('harlequin_')})), file=sys.stderr)\n"
        "print(','.join(m for m in ('duckdb', 'pyarrow', 'tomlkit') "
        "if m in sys.modules), file=sys.stderr)\n"
    )
    adapters, forbidden = proc.stderr.split("\n")[:2]
    assert not adapters
    assert not forbidden
