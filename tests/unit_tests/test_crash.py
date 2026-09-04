from __future__ import annotations

from pathlib import Path

import pytest

from harlequin.crash import (
    ACTIVE_BUFFER,
    CRASH_REPORT_KEEP,
    ISSUE_URL,
    build_crash_report,
    crash_message,
    root_cause,
    write_crash_report,
)
from harlequin.redact import REDACTED, hide_secrets_in


def raise_and_catch(error: BaseException) -> BaseException:
    """An exception with a real traceback on it, as a handler would receive."""
    try:
        raise error
    except BaseException as caught:
        return caught


def test_the_report_names_what_a_maintainer_needs() -> None:
    error = raise_and_catch(ValueError("no such column: foo"))

    report = build_crash_report(error, {"adapter": "duckdb"})

    assert "ValueError" in report
    assert "no such column: foo" in report
    assert "adapter: duckdb" in report
    assert "raise error" in report  # the frames
    assert "version" in report
    assert ISSUE_URL in report


def test_the_report_prints_no_local_variables() -> None:
    """Textual's own handler renders with show_locals=True. That is what this
    replaces."""

    def frame_with_a_secret_local() -> None:
        connection_string = "postgres://u:hunter2-and-more@host/db"  # noqa: F841
        raise ValueError("boom")

    try:
        frame_with_a_secret_local()
    except ValueError as e:
        report = build_crash_report(e, {})

    assert "hunter2-and-more" not in report
    assert "connection_string" not in report


def test_the_report_prints_no_registered_secret() -> None:
    hide_secrets_in({"conn_str": ["postgres://u:hunter2-and-more@host/db"]})
    error = raise_and_catch(
        ValueError("could not connect to postgres://u:hunter2-and-more@host/db")
    )

    report = build_crash_report(error, {})

    assert "hunter2-and-more" not in report
    assert REDACTED in report


def test_the_report_pastes_into_a_fenced_block() -> None:
    """A report that carried its own fences would break out of the issue's."""
    error = raise_and_catch(ValueError("boom"))

    report = build_crash_report(error, {ACTIVE_BUFFER: "select 1"})

    assert "```" not in report


def test_the_report_ends_with_the_active_buffer() -> None:
    """Under a heading that says what it is: the user decides whether to paste it."""
    error = raise_and_catch(ValueError("boom"))

    report = build_crash_report(error, {"adapter": "duckdb", ACTIVE_BUFFER: "select 1"})

    assert report.index("SQL IN THE ACTIVE BUFFER") > report.index("TRACEBACK")
    assert report.rstrip().endswith("select 1")
    # and not a second time, among the facts
    assert report.count("select 1") == 1


def test_root_cause_unwraps_a_worker_failure() -> None:
    """Two `@work` decorators reach the handler wrapped. A report naming
    `WorkerFailed` is a useless report."""

    class WorkerFailed(Exception):
        def __init__(self, error: BaseException) -> None:
            super().__init__("Worker raised exception")
            self.error = error

    cause = ValueError("the real problem")

    assert root_cause(WorkerFailed(cause)) is cause
    assert root_cause(cause) is cause


def test_the_report_names_both_halves_of_a_wrapped_error() -> None:
    class WorkerFailed(Exception):
        def __init__(self, error: BaseException) -> None:
            super().__init__("Worker raised exception")
            self.error = error

    error = raise_and_catch(WorkerFailed(raise_and_catch(ValueError("boom"))))

    report = build_crash_report(error, {})

    assert report.index("boom") < report.index("RAISED AS")
    assert "WorkerFailed" in report


def test_write_crash_report_keeps_the_newest(
    crash_reports_go_to_tmp: Path,
) -> None:
    crash_reports_go_to_tmp.mkdir(parents=True, exist_ok=True)
    for n in range(CRASH_REPORT_KEEP + 5):
        (crash_reports_go_to_tmp / f"crash-2020010{n:02d}T000000Z-1.log").touch()

    path = write_crash_report("a report")

    assert path is not None
    assert path.read_text() == "a report"
    assert len(list(crash_reports_go_to_tmp.glob("crash-*.log"))) == CRASH_REPORT_KEEP
    # a crash loop cannot destroy the evidence of the crash that started it
    assert path.exists()


def test_write_crash_report_returns_none_when_it_cannot_write(
    monkeypatch: pytest.MonkeyPatch, crash_reports_go_to_tmp: Path
) -> None:
    def _deny(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "mkdir", _deny)

    assert write_crash_report("a report") is None


def test_the_message_says_what_to_do_next(tmp_path: Path) -> None:
    error = raise_and_catch(ValueError("boom"))

    message = crash_message(tmp_path / "crash.log", error, saved=True)

    assert "ValueError: boom" in message
    assert str(tmp_path / "crash.log") in message
    assert ISSUE_URL in message
    assert "saved" in message


def test_the_message_claims_no_save_that_did_not_happen() -> None:
    error = raise_and_catch(ValueError("boom"))

    message = crash_message(None, error, saved=False)

    assert "saved" not in message
    assert ISSUE_URL in message
