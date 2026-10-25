from __future__ import annotations

import locale
import subprocess
import time
from pathlib import Path
from typing import Callable

import pytest
from rich.console import Console
from rich.panel import Panel

from harlequin.app_base import _as_markup
from harlequin.crash import crash_message
from harlequin.exception import (
    HarlequinConnectionError,
    HarlequinCrashError,
    HarlequinError,
    HarlequinQueryError,
    HarlequinTzDataError,
    pretty_error_message,
)
from harlequin.locale_manager import set_locale
from harlequin.windows_timezone import TZ_DATA_DOCS_URL, download_tzdata

# Issue #1137, plus a title that itself contains a tag and an injected link.
_LITERAL_CASES: tuple[tuple[str, str], ...] = (
    ("no column [amount] in table [orders]", "Query Error"),
    ("syntax error near [dbo].[users]", "Query Error"),
    ("column [red]count[/red] is ambiguous", "Query Error"),
    ("[Microsoft][ODBC Driver 17]Login failed.", "Query Error"),
    ("plain message", "missing [amount]"),
    ("[link=https://evil.example/]click[/link]", "Query Error"),
)


def _plain(panel: Panel, *, width: int = 200) -> str:
    """Rich's own plain-text export of a panel. Not a hand-rolled tag stripper."""
    console = Console(
        width=width,
        record=True,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    console.print(panel)
    return console.export_text()


def _ansi(panel: Panel, *, width: int = 120) -> str:
    console = Console(
        width=width,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    with console.capture() as capture:
        console.print(panel)
    return capture.get()


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


def test_error_panel_keeps_bracketed_text_literal() -> None:
    for message, title in _LITERAL_CASES:
        error = HarlequinQueryError(msg=message, title=title)
        first = _plain(pretty_error_message(error))
        second = _plain(pretty_error_message(error))
        assert first == second
        assert message in first
        assert title in first
        if "evil.example" in message:
            ansi = _ansi(pretty_error_message(error))
            assert "[link=https://evil.example/]click[/link]" in ansi
            assert "\x1b]8;" not in ansi


def test_pretty_print_error_keeps_brackets_on_stderr(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    code = (
        "from harlequin.exception import HarlequinQueryError, pretty_print_error\n"
        "pretty_print_error(HarlequinQueryError(\n"
        "    'no column [amount] in table [orders]', 'Query Error'))\n"
    )
    first = run_python(code)
    second = run_python(code)
    assert first.stdout == ""
    assert second.stdout == ""
    assert first.stderr == second.stderr
    assert "[amount]" in first.stderr
    assert "[orders]" in first.stderr


def test_startup_connection_failure_keeps_driver_brackets() -> None:
    """Same wrap as a failed connect: a non-Harlequin exception becomes the msg."""
    driver_error = RuntimeError(
        "no column [amount] in table [orders] [link=https://evil.example/]click[/link]"
    )
    title = getattr(
        driver_error,
        "title",
        "Harlequin could not connect to your database.",
    )
    error = (
        driver_error
        if isinstance(driver_error, HarlequinError)
        else HarlequinConnectionError(msg=str(driver_error), title=str(title))
    )
    plain = _plain(pretty_error_message(error))
    assert "[amount]" in plain
    assert "[orders]" in plain
    assert "[link=https://evil.example/]click[/link]" in plain


def test_omitted_title_stays_default_and_literal() -> None:
    plain = _plain(pretty_error_message(HarlequinError("boom [amount]")))
    assert "Harlequin encountered an error." in plain
    assert "[amount]" in plain


def test_long_bracketed_message_is_returned_intact() -> None:
    line = "no column [amount] in table [orders]"
    repeats = (100_000 // (len(line) + 1)) + 1
    message = "\n".join([line] * repeats)
    assert len(message) >= 100_000
    error = HarlequinQueryError(msg=message, title="Query Error")
    started = time.perf_counter()
    plain = _plain(pretty_error_message(error), width=len(line) + 20)
    elapsed = time.perf_counter() - started
    assert plain.count("[amount]") == message.count("[amount]")
    assert plain.count("[orders]") == message.count("[orders]")
    assert elapsed < 2.0, f"render took {elapsed:.3f}s"


def test_subclass_that_does_not_opt_in_stays_literal() -> None:
    class LegacyDriverError(HarlequinError):
        def __init__(self, msg: str, title: str = "") -> None:
            Exception.__init__(self, msg)
            self.msg = msg
            self.title = title

    message = "no column [amount] in table [orders]"
    title = "missing [amount]"
    error = LegacyDriverError(message, title)
    # Inherited default. The subclass never opts in, so the panel stays literal.
    assert error.markup is False
    plain = _plain(pretty_error_message(error))
    assert message in plain
    assert title in plain


def test_constructor_accepts_message_and_title_without_markup() -> None:
    positional = HarlequinQueryError("hello [amount]", "Query Error")
    assert positional.msg == "hello [amount]"
    assert positional.title == "Query Error"
    assert positional.markup is False
    keyword = HarlequinError(msg="m", title="t")
    assert keyword.markup is False
    ctor: Callable[..., HarlequinError] = HarlequinError
    with pytest.raises(TypeError):
        ctor("m", "t", True)


def test_timezone_download_failure_keeps_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign = (
        "no column [amount] in table [orders]; [link=https://evil.example/]click[/link]"
    )

    def urlopen(*_args: object, **_kwargs: object) -> None:
        raise OSError(foreign)

    monkeypatch.setattr("harlequin.windows_timezone.urlopen", urlopen)
    with pytest.raises(HarlequinTzDataError) as exc_info:
        download_tzdata()

    error = exc_info.value
    assert isinstance(error.__cause__, OSError)
    plain = _plain(pretty_error_message(error), width=240)
    ansi = _ansi(pretty_error_message(error), width=240)
    assert TZ_DATA_DOCS_URL in plain
    assert "[amount]" in plain
    assert "[orders]" in plain
    assert "[link=https://evil.example/]click[/link]" in plain
    assert "\x1b]8;" not in ansi


def test_crash_panel_keeps_link_markup_and_literal_exception() -> None:
    message = crash_message(
        Path("/tmp/crash.log"),
        RuntimeError("no column [amount] in orders"),
        saved=False,
    )
    panel = pretty_error_message(
        HarlequinCrashError(_as_markup(message), title="Harlequin crashed.")
    )
    rendered = _ansi(panel, width=100)
    assert "\x1b]8;" in rendered
    assert "[amount]" in rendered


def test_locale_warnings_keep_authored_bold(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_en_us(_category: int, locale_name: str = "") -> str:
        if locale_name == "en_US.UTF-8":
            raise locale.Error("unsupported")
        return "C"

    monkeypatch.setattr("harlequin.locale_manager.locale.setlocale", fail_en_us)
    failed = _capture_warning(lambda: set_locale(""))
    assert "--locale" in failed
    assert "--locale C" in failed
    assert "[bold]" not in failed

    def allow_en_us(_category: int, locale_name: str = "") -> str:
        if locale_name == "en_US.UTF-8":
            return "en_US.UTF-8"
        return "C"

    monkeypatch.setattr("harlequin.locale_manager.locale.setlocale", allow_en_us)
    assumed = _capture_warning(lambda: set_locale(""))
    assert "--locale" in assumed
    assert "--locale C" in assumed
    assert "[bold]" not in assumed
    assert failed != assumed


def _capture_warning(callback: Callable[[], None]) -> str:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        callback()
    return buf.getvalue()
