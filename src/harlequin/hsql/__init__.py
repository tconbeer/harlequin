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
    from harlequin.hsql.cli import run

    sys.exit(run(argv))
