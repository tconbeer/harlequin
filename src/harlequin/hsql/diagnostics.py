"""Everything hsql writes to stderr, and the code it exits with.

stdout is data: result sets, and nothing else. Timings, row counts, truncation
notices and errors go here, so `hsql -c ... --csv > out.csv` produces a clean
file while the caller still sees what happened.

Exit codes are hsql's contract rather than Harlequin's, which is why the
mapping lives here and not in `harlequin.exception`.
"""

from __future__ import annotations

import json
import sys
from enum import IntEnum
from typing import Any, Sequence

from harlequin.exception import (
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinError,
)

PROGRAM = "hsql"

IDE_THEMES = frozenset(
    {
        "atom-one-dark",
        "atom-one-light",
        "catppuccin-frappe",
        "catppuccin-latte",
        "catppuccin-macchiato",
        "catppuccin-mocha",
        "dracula",
        "flexoki",
        "gruvbox",
        "harlequin",
        "monokai",
        "nord",
        "rose-pine",
        "rose-pine-dawn",
        "rose-pine-moon",
        "solarized-dark",
        "solarized-light",
        "textual-dark",
        "textual-light",
        "tokyo-night",
    }
)
"""Every name `harlequin -t` takes, spelled out rather than imported.

`harlequin.colors` is where these live, and reaching it would import Textual --
the one thing this package may never do. So they are copied, and
`tests/unit_tests/test_hsql.py` asserts the copy still matches.
"""


class ExitCode(IntEnum):
    OK = 0
    QUERY = 1
    """The database rejected the SQL."""

    USAGE = 2
    """A bad flag, a bad profile, or a config file hsql could not read."""

    CONNECTION = 3
    TIMEOUT = 4
    """Unused until `--timeout` lands; the number is reserved for it."""

    INTERRUPT = 130


def exit_code_for(error: BaseException) -> ExitCode:
    """The code hsql exits with, having failed with `error`."""
    if isinstance(error, KeyboardInterrupt):
        return ExitCode.INTERRUPT
    if isinstance(error, HarlequinConfigError):
        return ExitCode.USAGE
    if isinstance(error, HarlequinConnectionError):
        return ExitCode.CONNECTION
    return ExitCode.QUERY


def error(message: str) -> None:
    """One plain line, prefixed with the program name.

    No panel, no ANSI, no box drawing: those are affordances of a full-screen
    app, and here they would be something a caller has to parse around.
    """
    _write(f"{PROGRAM}: error: {message}")


def report_error(exception: BaseException) -> None:
    message = (
        exception.msg if isinstance(exception, HarlequinError) else str(exception)
    ) or type(exception).__name__
    error(message)


def note(message: str) -> None:
    _write(f"note: {message}")


def report_theme_confusion(conn_str: Sequence[str]) -> None:
    """Say what `-t` did, when it was probably meant as `--theme`.

    `-t` is `--theme` in the IDE and "tuples only" in psql, and hsql takes
    psql's meaning because `-tAc` is one idiom rather than three flags. So
    `hsql -t nord -c ...` parses cleanly and wrongly: `-t` is a switch and
    `nord` becomes a connection string. Nothing here fails -- DuckDB will
    happily create a database file named `nord` -- which is exactly why it is
    worth a line on stderr.

    Only called when `-t` was passed on the command line, so a theme name is a
    coincidence rather than the explanation.
    """
    themed = next((s for s in conn_str if s in IDE_THEMES), None)
    if themed is None:
        return
    note(
        f"hsql has no themes; -t is --tuples-only, as in psql, "
        f"so {themed!r} was read as a connection string."
    )


def report_truncation(max_rows: int) -> None:
    """Say that a result was cut short.

    Fires even under `-t`, which suppresses stdout chrome and not warnings: a
    flag that silently defeated this would undo the promise it exists beside.
    """
    note(f"results truncated at {max_rows} rows (--limit)")


def report_row_count(rows: int, elapsed: float, truncated: bool) -> None:
    """The line psql puts under a result, on the stream that isn't the data.

    A truncated count reads `500 of 500+` because the true total is unknowable
    under a hard fetch limit -- not paying for the rest is the point of it.
    """
    noun = "row" if rows == 1 else "rows"
    count = f"{rows} of {rows}+ {noun}" if truncated else f"{rows} {noun}"
    _write(f"{count} in {elapsed:.2f}s")


def report_stats(
    *,
    status: str,
    statements: int,
    rows: int,
    truncated: bool,
    limit: int | None,
    elapsed_ms: int,
    columns: Sequence[tuple[str, str]],
    message: str | None = None,
) -> None:
    """One line of JSON, whatever stdout is carrying.

    This is how a caller gets structured metadata about a run without polluting
    the data -- and it reads the same whether stdout held a csv or a parquet.
    """
    payload: dict[str, Any] = {
        "status": status,
        "statements": statements,
        "rows": rows,
        "truncated": truncated,
        "limit": limit,
        "elapsed_ms": elapsed_ms,
        "columns": [{"name": name, "type": type_} for name, type_ in columns],
    }
    if message is not None:
        payload["error"] = message
    _write(json.dumps(payload, separators=(",", ":")))


def _write(line: str) -> None:
    # sys.stderr is resolved on each call, not bound at import: a test harness
    # that swaps the stream out has to be able to see what was written.
    print(line, file=sys.stderr)
