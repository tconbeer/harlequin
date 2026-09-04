"""The file Harlequin and hsql write when they hit a bug in themselves.

A user should never see a raw traceback: it is intimidating, it says nothing
about what to do next, and Textual renders it with `show_locals=True`, which
puts connection strings, tokens and query results on the terminal. Instead both
commands write everything worth having to a file, print where it is, and point
at an issue template built to receive it.

Headless, because `hsql` writes one too, and because a crash handler that has
to import the world to report a crash is a fresh place to crash.
"""

from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from platformdirs import user_log_path

from harlequin.environment import runtime_report
from harlequin.redact import redact_text

CRASH_REPORT_KEEP = 10
"""How many reports are kept. Distinct filenames, so a crash loop cannot
destroy the evidence of the first crash."""

ISSUE_URL = "https://github.com/tconbeer/harlequin/issues/new?template=crash_report.md"

ACTIVE_BUFFER = "active_buffer"
"""The one context key that is rendered as its own section rather than a fact."""

_RULE = "=" * 72


def get_crash_report_dir() -> Path:
    """Where reports are written: the log dir, not the cache dir.

    A cache is what a user is told to delete when things go wrong, and this is
    the file we are asking them to keep.
    """
    return user_log_path(appname="harlequin", appauthor=False)


def root_cause(error: BaseException) -> BaseException:
    """The error a report should be about, unwrapping Textual's `WorkerFailed`.

    By attribute rather than by class, so this module stays headless. A report
    naming `WorkerFailed` is a useless report.
    """
    wrapped = getattr(error, "error", None)
    if isinstance(wrapped, BaseException):
        return wrapped
    return error


def build_crash_report(
    error: BaseException,
    context: Mapping[str, Any],
    program: str = "harlequin",
) -> str:
    """The whole report, as the plain text it is written and pasted as.

    Not markdown: the user pastes this into a fenced block in an issue, and a
    report's own fences would break out of it. Everything goes through
    `redact_text()` once at the end -- one choke point, rather than a
    redaction each section has to remember.
    """
    cause = root_cause(error)
    facts = {key: value for key, value in context.items() if key != ACTIVE_BUFFER}
    sections = [
        _header(program),
        _section("ENVIRONMENT", _lines(runtime_report())),
        _section("CONTEXT", _lines(facts)),
        _section("TRACEBACK", _traceback(cause)),
    ]
    if cause is not error:
        sections.append(_section("RAISED AS", _traceback(error)))
    active_buffer = context.get(ACTIVE_BUFFER)
    if active_buffer:
        # usually what is needed to reproduce a TUI bug. Last, and under a
        # heading that says what it is, because SQL holds table names and
        # literals: the user decides whether to paste it.
        sections.append(_section("SQL IN THE ACTIVE BUFFER", str(active_buffer)))
    return redact_text("\n".join(sections))


def write_crash_report(report: str) -> Path | None:
    """Write a report and prune the old ones. None if it could not be written.

    Raises nothing: it is called from a handler that must not be able to fail.
    """
    directory = get_crash_report_dir()
    now = datetime.now(timezone.utc)
    path = directory / f"crash-{now:%Y%m%dT%H%M%SZ}-{os.getpid()}.log"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8", errors="replace")
    except OSError:
        return None
    _prune(directory)
    return path


def crash_message(
    report_path: Path | None,
    error: BaseException,
    saved: bool,
    program: str = "harlequin",
) -> str:
    """What the user reads on their terminal: what broke, and what to do now."""
    cause = root_cause(error)
    lines = [f"{type(cause).__name__}: {cause}", ""]
    if saved:
        lines.append(
            "Your open buffers were saved, and Harlequin will offer them back "
            "the next time you start it."
        )
    if report_path is not None:
        lines.extend(
            [
                f"A crash report was written to {report_path}.",
                "",
                f"Please review that file, then report this bug with it at {ISSUE_URL}",
            ]
        )
    else:
        lines.append(f"Please report this at {ISSUE_URL}.")
    return "\n".join(lines)


def _header(program: str) -> str:
    written = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    return (
        f"{program} crash report, written {written}.\n"
        "\n"
        "Please review this file before sharing it: it holds your configuration\n"
        "(with passwords masked) and the SQL that was in your active buffer.\n"
        f"Report this at {ISSUE_URL}\n"
    )


def _section(title: str, body: str) -> str:
    return f"\n{_RULE}\n=== {title} ===\n{_RULE}\n\n{body.rstrip()}\n"


def _lines(facts: Mapping[str, Any], indent: str = "") -> str:
    """A mapping as `key: value` lines, one level of nesting indented under it."""
    rendered = []
    for key, value in facts.items():
        if isinstance(value, Mapping):
            rendered.append(f"{indent}{key}:")
            rendered.append(_lines(value, indent + "  "))
        else:
            rendered.append(f"{indent}{key}: {value}")
    return "\n".join(rendered)


def _traceback(error: BaseException) -> str:
    """The frames, and never the locals: that is what this replaces."""
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


def _prune(directory: Path) -> None:
    try:
        reports = sorted(
            directory.glob("crash-*.log"), key=lambda p: p.name, reverse=True
        )
        for stale in reports[CRASH_REPORT_KEEP:]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass
