"""Every long flag the packaged README and the skill type is a flag hsql has.

The README is the first thing a person reads about hsql and the skill is the
first thing an agent reads; a command line in either is one that gets run
verbatim. Nothing else in this repo reads them: the CLI is tested thoroughly and
its documentation is not, so a flag that was renamed, or one that never existed,
sits in a fenced code block until someone pastes it and gets `No such option`
and exit 2.

So: pull every `--long-flag` out of the prose and the fences alike, and check
each one against the document hsql writes about itself. Long spellings only --
`-tAc` is a bundle of three, and taking single-dash tokens apart is guesswork
this does not need to do. What it cannot check is a flag spelled correctly that
the prose then describes wrongly; that stays a review problem.
"""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path

import pytest

from harlequin.hsql.modes import spec

REPO_ROOT = Path(__file__).resolve().parents[2]
HSQL_README = REPO_ROOT / "packaging" / "hsql" / "README.md"
SKILL_DIR = REPO_ROOT / "src" / "harlequin" / "hsql" / "skill"

DOCUMENTS = [
    HSQL_README,
    SKILL_DIR / "SKILL.md",
    *sorted(SKILL_DIR.glob("references/*.md")),
]
"""Every file in this repo that types an hsql command line at a reader."""

LONG_FLAG = re.compile(r"--[a-zA-Z][a-zA-Z0-9_-]*")
"""A long option as a command line spells one, underscores included, since an
adapter may declare `--md_token`. Stops at `=`, so `--format=csv` is `--format`."""

OTHER_PROGRAMS = {
    "--with": "uv, in the README's install lines",
    "--pset": "psql, in the README's differences table",
}
"""Flags the README types that belong to another program. Named one at a time,
with the reason, so that excusing a flag is a decision someone made rather than
a pattern that quietly swallowed the next typo."""


def documented_flags(text: str) -> set[str]:
    """Every long flag a document types, minus the ones another program owns."""
    return set(LONG_FLAG.findall(text)) - set(OTHER_PROGRAMS)


def hsql_flags() -> set[str]:
    """Every long flag `hsql --spec` reports: hsql's own, and each adapter's.

    Read out of the document rather than off the click command, so that this
    checks the surface hsql advertises -- an adapter option whose spelling
    collides with one of hsql's is dropped from both.
    """
    document = BytesIO()
    spec.report(document, adapter=None, format_name=spec.JSON, format_chosen=False)
    reported = json.loads(document.getvalue())
    declared = [
        *reported["options"],
        *(
            option
            for adapter in reported["adapters"].values()
            for option in adapter["options"] or []
        ),
    ]
    return {
        decl for option in declared for decl in option["decls"] if decl.startswith("--")
    }


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_every_flag_a_document_types_exists(document: Path) -> None:
    """The one that fails when a documented command line would exit 2."""
    missing = documented_flags(document.read_text(encoding="utf-8")) - hsql_flags()
    assert not missing, (
        f"{document.name} documents flags hsql does not have: "
        f"{', '.join(sorted(missing))}. Run `hsql --spec` for the real spellings."
    )


def test_the_skill_is_covered() -> None:
    """The parametrization reads the skill off the disk, so an empty glob would
    have passed silently -- and the references are where the flags are."""
    names = {path.name for path in DOCUMENTS}
    assert "SKILL.md" in names
    assert len(DOCUMENTS) >= 4


def test_the_allowlist_names_only_flags_hsql_does_not_have() -> None:
    """An entry that shadowed a real flag would hide the next rename of it."""
    assert not set(OTHER_PROGRAMS) & hsql_flags()


def test_the_allowlist_names_only_flags_a_document_still_types() -> None:
    """So an entry outlives the line it was written for by one PR at most."""
    typed = {
        flag
        for document in DOCUMENTS
        for flag in LONG_FLAG.findall(document.read_text(encoding="utf-8"))
    }
    assert set(OTHER_PROGRAMS) <= typed


def test_the_extractor_reads_flags_out_of_prose_and_out_of_fences() -> None:
    assert documented_flags(
        "Pass `--read-only`, or `--limit -1`.\n"
        "```bash\n"
        "$ hsql -tAc 'select 1' --format=csv --md_token TOKEN -o out.csv\n"
        "```\n"
        "A negative number -- like -1 -- is unlimited.\n"
    ) == {"--read-only", "--limit", "--format", "--md_token"}
