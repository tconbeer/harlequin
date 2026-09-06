from __future__ import annotations

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
