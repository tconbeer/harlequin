"""hsql -- Harlequin, headless.

The same adapters, config files, profiles and execution core as the IDE, with
no Textual anywhere in the import graph. It is a second console script rather
than a flag on `harlequin`, so that the IDE stays free to evolve while this
side's output format and exit codes are an API.

`main()` imports nothing beyond stdlib before it knows which of two things this
invocation is. An invocation that belongs to a warm session (`--session`, or
`HSQL_SESSION`) is answered by a process that already holds the connection, and
what it costs the caller is this interpreter starting -- so the decision has to
come before `import click`, which on its own costs more than the whole round
trip. Everything else falls through to the command in `harlequin.hsql.cli`.
"""

from __future__ import annotations

import os
import sys
from typing import Any, NoReturn, Sequence

__all__ = ["main"]


def main() -> None:
    """The `hsql` console script."""
    argv = sys.argv[1:]
    try:
        code = _run_warm(argv)
    except BaseException as e:
        _report_crash(e, argv)
    if code is not None:
        sys.exit(code)
    _run_cold(argv)


def _run_warm(argv: list[str]) -> int | None:
    """What the session that answered exited with, or None to run cold.

    None where this invocation names no session at all, and where it names an
    ambient one that is not running -- the client has warned by then, and a
    cold run is what the caller wanted either way.
    """
    from harlequin.hsql.session import requested_session

    session = requested_session(argv, os.environ)
    if session is None:
        return None

    from harlequin.hsql.client import INTERRUPT, run

    try:
        return run(session, argv, os.environ)
    except KeyboardInterrupt:
        return INTERRUPT


def _run_cold(argv: list[str]) -> None:
    """A fresh process, a fresh connection, and no memory of the last one."""
    from harlequin.hsql.cli import run

    try:
        code = run(argv)
    except BaseException as e:
        # a bug in hsql itself, rather than anything the run was asked to do:
        # `run()` has already turned a bad flag and an interrupt into a code
        _report_crash(e, argv)
    sys.exit(code)


def _report_crash(error: BaseException, argv: Sequence[str]) -> NoReturn:
    """Exit `CRASH` over a bug in hsql, having written a report about it.

    Python's own handler would print a traceback and exit 1, which is the code
    for a query the database rejected: a caller scripting against these could
    not tell the two apart. Both entry points end here, since a bug in the
    client is as much hsql's as a bug in the command.
    """
    from harlequin.crash import build_crash_report, write_crash_report
    from harlequin.hsql import diagnostics
    from harlequin.hsql.cli import PROGRAM
    from harlequin.hsql.diagnostics import ExitCode

    report_path = None
    try:
        report_path = write_crash_report(
            build_crash_report(error, _crash_context(argv), program=PROGRAM)
        )
    except BaseException:
        pass
    diagnostics.report_crash(report_path)
    sys.exit(ExitCode.CRASH)


def _crash_context(argv: Sequence[str]) -> dict[str, Any]:
    """What the run was, for the crash report.

    The argv is masked by `redact_conn_str()` before the report's own pass: a
    crash during parsing happens before `hide_secrets_in()` runs, so the
    registered-secret set is empty and span-masking is the only thing that
    catches a DSN typed on the command line.
    """
    from harlequin.redact import redact_conn_str

    context: dict[str, Any] = {}
    try:
        context["argv"] = " ".join(redact_conn_str(list(argv)))
    except Exception:
        context["argv"] = "unknown"
    for key, flags in (
        ("adapter", ("-a", "--adapter")),
        ("profile", ("-P", "--profile")),
    ):
        context[key] = _value_after(argv, flags)
    return context


def _value_after(argv: Sequence[str], flags: Sequence[str]) -> str | None:
    """What a flag was given on the command line, read off argv.

    Read here rather than taken from the parsed command: a crash during parsing
    is one of the crashes this reports, and there is nothing parsed to ask.
    """
    for index, arg in enumerate(argv):
        if arg in flags and index + 1 < len(argv):
            return argv[index + 1]
        for flag in flags:
            if arg.startswith(f"{flag}="):
                return arg[len(flag) + 1 :]
    return None
