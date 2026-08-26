"""Everything hsql writes to stderr, and the code it exits with.

stdout is data: result sets, and nothing else. Truncation notices, errors and
`--stats` go here, so `hsql -c ... --csv > out.csv` produces a clean file while
the caller still sees what happened.

Nothing here restates what stdout already carries. A result's row count is its
footer, where psql puts it and where `-t` can decline it; timings are a field of
`--stats`. A quiet run is silent on this stream.

Exit codes are hsql's contract rather than Harlequin's, which is why the
mapping lives here and not in `harlequin.exception`.

Nothing written here carries a secret, either: every line goes through
`harlequin.redact`, which covers the one channel nothing can shape in advance
-- a driver exception that quotes back the DSN it was handed.
"""

from __future__ import annotations

import json
import sys
from enum import IntEnum
from pathlib import Path
from typing import Any, Sequence

from harlequin.exception import (
    HarlequinCatalogPathError,
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinError,
)
from harlequin.redact import redact_text

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
    if isinstance(error, (HarlequinCatalogPathError, HarlequinConfigError)):
        # a path that names nothing is a bad argument, not a failed query: the
        # catalog answered, and what it answered is that there is no such item.
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

    Names the flag and its remedy rather than the row count: the count is
    already under the result, as `500 of >500`, and what a caller cannot read
    off stdout is what to pass to get the rest.
    """
    note(f"results truncated at --limit {max_rows}; pass --limit -1 for all rows")


def report_row_cap(shown: int, of: int) -> None:
    """Say that the layout printed fewer rows than were fetched.

    Unlike truncation this is only worth saying when the footer cannot: it
    reads `40 of 500 rows` there, and this stream does not restate stdout. With
    `-t` or `--no-footer` there is no footer, and a short result that says
    nothing about being short is the thing a caller cannot detect.
    """
    note(f"printed {shown} of {of} rows; pass --display-rows -1 for all of them")


def report_row_cap_ignored(format_name: str) -> None:
    """Say that `--display-rows` does not reach the format that was chosen.

    It caps a layout, and a file format has no layout. Silence here would read
    as a cap that was applied, which is the one way this could mislead: the
    file would hold every row the caller thought they had capped.
    """
    note(
        f"--display-rows caps the text layouts, so it had no effect on "
        f"{format_name}; use --limit to fetch fewer rows"
    )


def report_limit_ignored(mode: str) -> None:
    """Say that `--limit` does not reach the listing `mode` printed.

    It is the *hard* limit -- fewer rows leave the database -- and a catalog
    listing is however many objects the adapter reported. Silence would read as
    a limit that was applied, and a caller who thinks they capped a listing at
    ten and got four hundred rows has no way to tell which happened.
    """
    note(
        "--limit fetches fewer rows from the database, so it had no effect on "
        f"{mode}; use --display-rows to print fewer of them"
    )


def report_document_format_ignored(mode: str, format_name: str) -> None:
    """Say that `--format` does not reach a mode that writes a document.

    `--config show` is TOML or JSON, not a result set, so the formats that
    arrange rows have nothing to arrange. Silence would read as a format that
    was applied, which is the same way `--display-rows` could mislead, and it
    gets the same kind of line.
    """
    note(
        f"{mode} writes a document, so --format {format_name} had no effect; "
        "--format json is the machine-readable one"
    )


def report_written(paths: Sequence[Path]) -> None:
    """Name the files a directory `-o` wrote, since the caller did not name them.

    A file the caller named needs no line -- they know where it is. What hsql
    chose is the one thing about the run that stdout cannot carry.
    """
    if not paths:
        return
    if len(paths) == 1:
        note(f"wrote {paths[0]}")
    else:
        listed = ", ".join(path.name for path in paths)
        note(f"wrote {len(paths)} files to {paths[0].parent}: {listed}")


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
    # stdout first, always. It is block-buffered when it is a pipe, and stderr
    # is not, so a diagnostic written now would otherwise overtake the result
    # set it describes -- and the two streams would interleave differently on a
    # terminal than in a pipe. Flushing here rather than at each call site keeps
    # that true for errors and --stats as well as for notes.
    sys.stdout.flush()
    # sys.stderr is resolved on each call, not bound at import: a test harness
    # that swaps the stream out has to be able to see what was written.
    #
    # Redacting here rather than at each call site is what makes this a
    # promise: an error raised by a driver, a note, a `--stats` payload with a
    # message in it, and whatever is added next all leave through this line.
    print(redact_text(line), file=sys.stderr)
