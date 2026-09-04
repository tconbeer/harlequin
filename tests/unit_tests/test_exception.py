from __future__ import annotations

import subprocess
from typing import Callable

import pytest
from rich.console import Console

from harlequin.exception import (
    HarlequinError,
    HarlequinQueryError,
    pretty_error_message,
)


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


def render_error(error: HarlequinError) -> str:
    """The panel as a terminal receives it, escape sequences and all."""
    console = Console(
        width=100,
        force_terminal=True,
        legacy_windows=False,
        no_color=False,
        _environ={"TERM": "xterm-256color"},
    )
    with console.capture() as capture:
        console.print(pretty_error_message(error))
    return capture.get()


@pytest.mark.parametrize(
    ("msg", "must_contain"),
    [
        ("no column [amount] in table [orders]", ("[amount]", "[orders]")),
        ("syntax error near [dbo].[users]", ("[dbo]", "[users]")),
        ("column [red]count[/red] is ambiguous", ("[red]count[/red]",)),
        (
            "[Microsoft][ODBC Driver 17]Login failed.",
            ("[Microsoft]", "[ODBC Driver 17]"),
        ),
    ],
)
def test_pretty_error_message_prints_bracketed_text(
    msg: str, must_contain: tuple[str, ...]
) -> None:
    """A driver quotes identifiers in brackets; those are text, not markup."""
    rendered = render_error(HarlequinQueryError(msg=msg, title="Query Error"))
    for fragment in must_contain:
        assert fragment in rendered


def test_pretty_error_message_prints_bracketed_title() -> None:
    rendered = render_error(HarlequinQueryError(msg="ok", title="Error in [orders]"))
    assert "[orders]" in rendered


def test_pretty_error_message_markup_opt_in_keeps_links_clickable() -> None:
    """Authored markup still renders: a `[link]` becomes an OSC-8 hyperlink."""
    url = "https://harlequin.sh/docs"
    rendered = render_error(
        HarlequinError(f"see [link={url}]{url}[/link]", title="Info", markup=True)
    )
    assert "\x1b]8;" in rendered
    assert url in rendered
