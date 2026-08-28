"""`--skill`: the Agent Skill for driving hsql, straight out of the wheel.

The skill an agent installs is the skill for the `hsql` on that machine, which
is the one place "which version am I reading about" cannot be got wrong -- and
it needs no network, which is the environment a CLI-driving agent is most often
in. So the text ships as package data and this mode prints it.

**The skill is a directory, not a file.** `SKILL.md` is the standing guidance
that enters an agent's context whole, and it is kept small enough to be worth
that; the four references beside it are the depth, read only when the work
reaches one. So stdout gets `SKILL.md` -- the document, for a caller who wants
to read or pipe it -- and `-o` installs the whole tree, wherever it names.

It imports nothing: no adapter, no config file, no database. Printing a
packaged file should cost what printing a packaged file costs.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from harlequin.hsql import diagnostics
from harlequin.hsql.diagnostics import ExitCode

SKILL_DIR = Path(__file__).resolve().parent.parent / "skill"
"""Where the text lives, read the way the help screen reads its markdown."""

FILENAME = "SKILL.md"
"""What this document is called, on stdout's behalf and in a folder `-o` names.

Named by the Agent Skills spec rather than by us: a directory holding a file
called anything else is not a skill.
"""

REFERENCES = "references"
"""The subdirectory `SKILL.md` points at, and so the name it must keep."""

MARKDOWN = "markdown"
"""What this mode writes, whatever `--format` says. `none` writes nothing."""

NONE = "none"


def text() -> bytes:
    """`SKILL.md` exactly as it ships, bytes and all.

    Read in binary so that what `-o` writes and what stdout carries cannot
    differ by a line ending on the way through.
    """
    return (SKILL_DIR / FILENAME).read_bytes()


def reference_paths() -> list[Path]:
    """Every reference file, sorted, as paths inside the skill directory."""
    return sorted((SKILL_DIR / REFERENCES).glob("*.md"))


def report(out: BinaryIO, *, format_name: str, format_chosen: bool) -> ExitCode:
    """Write `SKILL.md` and return the code it exits with.

    Always 0: this mode reads one packaged file, and there is nothing about the
    installation it could find wrong.
    """
    if format_name == NONE:
        return ExitCode.OK
    if format_name != MARKDOWN and format_chosen:
        diagnostics.report_fixed_format_ignored(
            "--skill", format_name, written=MARKDOWN
        )
    out.write(text())
    return ExitCode.OK


def install_references(directory: Path) -> list[Path]:
    """Copy the references into `directory/references/`, and say what was written.

    Beside `SKILL.md` wherever `-o` put it, because a `SKILL.md` whose pointers
    dangle is worse than one that never had them: the agent reading it has no
    way to tell a reference that is missing from one that does not exist.
    """
    target = directory / REFERENCES
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for source in reference_paths():
        destination = target / source.name
        destination.write_bytes(source.read_bytes())
        written.append(destination)
    return written
