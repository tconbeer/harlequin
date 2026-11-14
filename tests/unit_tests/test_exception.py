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


def test_pretty_error_message_escapes_markup_in_error_text() -> None:
    """Error text that is not ours must not lose characters to rich markup."""
    import io

    from rich.console import Console

    from harlequin.exception import HarlequinQueryError, pretty_error_message

    buf = io.StringIO()
    Console(file=buf, width=60).print(
        pretty_error_message(
            HarlequinQueryError(
                msg="no column [amount] in table [orders]", title="Query Error"
            )
        )
    )
    out = buf.getvalue()
    assert "amount" in out
    assert "orders" in out


def test_pretty_error_message_honors_markup_opt_in() -> None:
    """A caller that sets markup=True still gets rich markup interpreted."""
    import io

    from rich.console import Console

    from harlequin.exception import HarlequinError, pretty_error_message

    buf = io.StringIO()
    Console(file=buf, width=60).print(
        pretty_error_message(
            HarlequinError(msg="use [bold]--locale[/]", title="x", markup=True)
        )
    )
    out = buf.getvalue()
    assert "--locale" in out
    assert "[bold]" not in out
