"""hsql -- Harlequin, headless.

The same adapters, config files, profiles and execution core as the IDE, with
no Textual anywhere in the import graph. It is a second console script rather
than a flag on `harlequin`, so that the IDE stays free to evolve while this
side's output format and exit codes are an API.
"""

from __future__ import annotations

import sys

from harlequin.hsql.diagnostics import ExitCode

__all__ = ["main"]


def main() -> None:
    """The `hsql` console script."""
    import click

    from harlequin.hsql.cli import PROGRAM, build_cli

    try:
        code = build_cli().main(prog_name=PROGRAM, standalone_mode=False)
    except click.ClickException as e:
        # parse-level failures -- an unknown option, a bad choice. click already
        # exits 2 for those, which is the code hsql documents for usage errors.
        e.show()
        sys.exit(e.exit_code)
    except (click.Abort, KeyboardInterrupt):
        sys.exit(ExitCode.INTERRUPT)
    sys.exit(code if isinstance(code, int) else ExitCode.OK)
