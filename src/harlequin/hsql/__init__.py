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
from typing import Any, Sequence

__all__ = ["main"]


def main() -> None:
    """The `hsql` console script."""
    from harlequin.hsql.session import requested_session

    argv = sys.argv[1:]
    session = requested_session(argv, os.environ)
    if session is not None:
        from harlequin.hsql.client import INTERRUPT, run

        try:
            code = run(session, argv, os.environ)
        except KeyboardInterrupt:
            sys.exit(INTERRUPT)
        if code is not None:
            sys.exit(code)
        # an ambient session that is not running: warned, and running cold

    _run_cold(argv)


def _run_cold(argv: list[str]) -> None:
    """A fresh process, a fresh connection, and no memory of the last one."""
    import click

    from harlequin.hsql import diagnostics
    from harlequin.hsql.cli import PROGRAM, build_cli
    from harlequin.hsql.diagnostics import ExitCode

    try:
        # the same arguments to both: which adapter's options the command
        # carries is decided from them, and then click parses them.
        code = build_cli(argv).main(args=argv, prog_name=PROGRAM, standalone_mode=False)
    except click.ClickException as e:
        # parse-level failures -- an unknown option, a bad choice. click already
        # exits 2 for those, which is the code hsql documents for usage errors.
        e.show()
        sys.exit(e.exit_code)
    except (click.Abort, KeyboardInterrupt):
        sys.exit(ExitCode.INTERRUPT)
    except BaseException as e:
        # a bug in hsql, rather than anything the run was asked to do. Python's
        # own handler would print a traceback and exit 1, which is the code for
        # a query the database rejected: a caller scripting against these could
        # not tell the two apart.
        from harlequin.crash import build_crash_report, write_crash_report

        report_path = None
        try:
            report_path = write_crash_report(
                build_crash_report(e, _crash_context(argv), program=PROGRAM)
            )
        except BaseException:
            pass
        diagnostics.report_crash(report_path)
        sys.exit(ExitCode.CRASH)
    sys.exit(code if isinstance(code, int) else ExitCode.OK)


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
