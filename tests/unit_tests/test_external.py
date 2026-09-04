from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence, cast

import pytest
from textual.app import App, SuspendNotSupported

from harlequin.exception import HarlequinExternalError
from harlequin.external import (
    ExternalEdit,
    launch_external_editor,
    resolve_editor,
    run_in_terminal,
    split_command,
)


class FakeApp:
    """The whole of what `run_in_terminal` uses of an App: a suspend()."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.suspend_count = 0
        self.resumed = False

    @contextmanager
    def suspend(self) -> Iterator[None]:
        if self.error is not None:
            raise self.error
        self.suspend_count += 1
        yield
        self.resumed = True


@pytest.fixture
def fake_app() -> FakeApp:
    return FakeApp()


@pytest.fixture(autouse=True)
def no_editor_in_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)


def test_visual_wins_over_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISUAL", "hx")
    monkeypatch.setenv("EDITOR", "vim")
    assert resolve_editor() == ["hx"]


def test_editor_is_used_when_visual_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", "vim")
    assert resolve_editor() == ["vim"]


def test_a_blank_variable_does_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISUAL", "   ")
    monkeypatch.setenv("EDITOR", "vim")
    assert resolve_editor() == ["vim"]


def test_editor_flags_are_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", "code --wait")
    assert resolve_editor() == ["code", "--wait"]


def test_no_editor_names_both_variables() -> None:
    with pytest.raises(HarlequinExternalError) as excinfo:
        resolve_editor()
    assert "$VISUAL" in excinfo.value.msg
    assert "$EDITOR" in excinfo.value.msg


def test_a_quoted_windows_path_is_one_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert split_command('"C:\\Program Files\\Editor\\ed.exe" --wait') == [
        "C:\\Program Files\\Editor\\ed.exe",
        "--wait",
    ]


def test_run_in_terminal_suspends_and_returns_the_status(fake_app: FakeApp) -> None:
    returncode = run_in_terminal(
        cast("App[None]", fake_app), [sys.executable, "-c", "raise SystemExit(3)"]
    )
    assert returncode == 3
    assert fake_app.suspend_count == 1
    assert fake_app.resumed


def test_run_in_terminal_reports_a_terminal_that_cannot_suspend() -> None:
    app = FakeApp(error=SuspendNotSupported("nope"))
    with pytest.raises(HarlequinExternalError) as excinfo:
        run_in_terminal(cast("App[None]", app), [sys.executable, "-c", ""])
    assert "suspend" in excinfo.value.msg


def test_run_in_terminal_reports_a_program_it_cannot_start(fake_app: FakeApp) -> None:
    with pytest.raises(HarlequinExternalError) as excinfo:
        run_in_terminal(cast("App[None]", fake_app), ["harlequin-no-such-editor"])
    assert "harlequin-no-such-editor" in excinfo.value.msg


def test_the_round_trip_is_a_sql_file(
    monkeypatch: pytest.MonkeyPatch, fake_app: FakeApp
) -> None:
    monkeypatch.setenv("EDITOR", "ed --wait")
    seen: list[Sequence[str]] = []

    def fake_run_in_terminal(app: App, argv: Sequence[str]) -> int:
        seen.append(argv)
        path = Path(argv[-1])
        assert path.read_text(encoding="utf-8") == "select 1"
        path.write_text("select 2\n", encoding="utf-8")
        return 0

    monkeypatch.setattr("harlequin.external.run_in_terminal", fake_run_in_terminal)

    edit = launch_external_editor(cast("App[None]", fake_app), "select 1")

    assert edit == ExternalEdit(returncode=0, text="select 2\n")
    assert list(seen[0][:2]) == ["ed", "--wait"]
    assert seen[0][-1].endswith(".sql")
    assert not Path(seen[0][-1]).exists()


def test_a_nonzero_exit_discards_the_edit(
    monkeypatch: pytest.MonkeyPatch, fake_app: FakeApp
) -> None:
    monkeypatch.setenv("EDITOR", "ed")
    paths: list[Path] = []

    def fake_run_in_terminal(app: App, argv: Sequence[str]) -> int:
        path = Path(argv[-1])
        paths.append(path)
        path.write_text("select 2", encoding="utf-8")
        return 1

    monkeypatch.setattr("harlequin.external.run_in_terminal", fake_run_in_terminal)

    edit = launch_external_editor(cast("App[None]", fake_app), "select 1")

    assert edit == ExternalEdit(returncode=1, text=None)
    assert not paths[0].exists()


def test_crlf_does_not_come_back_in_the_buffer(
    monkeypatch: pytest.MonkeyPatch, fake_app: FakeApp
) -> None:
    monkeypatch.setenv("EDITOR", "ed")

    def fake_run_in_terminal(app: App, argv: Sequence[str]) -> int:
        Path(argv[-1]).write_bytes(b"select 1\r\nfrom foo\r\n")
        return 0

    monkeypatch.setattr("harlequin.external.run_in_terminal", fake_run_in_terminal)

    edit = launch_external_editor(cast("App[None]", fake_app), "select 1")

    assert edit.text == "select 1\nfrom foo\n"


def test_the_temp_file_is_removed_after_an_error(
    monkeypatch: pytest.MonkeyPatch, fake_app: FakeApp
) -> None:
    monkeypatch.setenv("EDITOR", "ed")
    paths: list[Path] = []

    def fake_run_in_terminal(app: App, argv: Sequence[str]) -> int:
        paths.append(Path(argv[-1]))
        raise HarlequinExternalError(msg="boom", title="boom")

    monkeypatch.setattr("harlequin.external.run_in_terminal", fake_run_in_terminal)

    with pytest.raises(HarlequinExternalError):
        launch_external_editor(cast("App[None]", fake_app), "select 1")

    assert not paths[0].exists()
