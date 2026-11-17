from __future__ import annotations

import os
import subprocess
from typing import Callable


def test_pretty_print_warning_goes_to_stderr(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """stdout belongs to query output; diagnostics go to stderr.

    `pretty_print_error` has always got this right. `pretty_print_warning` used
    `rich.print`, which writes to stdout -- and it is reached from the CLI path
    by both the locale manager and the Windows tzdata installer.
    """
    proc = run_python(
        "from harlequin.exception import pretty_print_warning\n"
        "pretty_print_warning(title='A Title', message='A message.')\n"
    )
    assert proc.stdout == ""
    assert "A message." in proc.stderr


def _render(renderable: object) -> str:
    from rich.console import Console

    console = Console(width=80, record=True, file=open(os.devnull, "w"))
    console.print(renderable)
    return console.export_text()


def test_pretty_error_message_keeps_bracketed_text() -> None:
    """Bracketed text in an error is data (a column list, a path), not markup."""
    from harlequin.exception import HarlequinError, pretty_error_message

    error = HarlequinError("no column [amount] in [sales] or [/oops]", title="[Bad]")
    text = _render(pretty_error_message(error))
    assert "no column [amount] in [sales] or [/oops]" in text
    assert "[Bad]" in text
