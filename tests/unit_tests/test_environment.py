from __future__ import annotations

import json
import sys

import pytest

from harlequin.environment import (
    UNKNOWN,
    adapter_facts,
    install_facts,
    runtime_report,
    terminal_facts,
)


def test_runtime_report_is_json_serializable() -> None:
    """A crash report and `--info` both render it as JSON."""
    report = runtime_report()
    assert json.loads(json.dumps(report)) == report
    assert set(report) == {"version", "python", "platform", "terminal", "install"}
    assert report["python"]["executable"] == sys.executable


def test_runtime_report_imports_no_adapter() -> None:
    """It runs inside a crash handler, where importing the world is a fresh crash."""
    loaded_before = set(sys.modules)
    runtime_report()
    assert not {name for name in set(sys.modules) - loaded_before if "adapter" in name}


def test_terminal_facts_report_that_a_session_is_remote_and_nothing_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whether the session is remote explains a bug; where it came from does not."""
    monkeypatch.setenv("SSH_CONNECTION", "10.1.2.3 55555 10.1.2.4 22")

    facts = terminal_facts()

    assert facts["ssh"] is True
    assert "10.1.2.3" not in json.dumps(facts)


def test_terminal_facts_when_the_environment_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "TERM",
        "TERM_PROGRAM",
        "TERM_PROGRAM_VERSION",
        "COLORTERM",
        "SHELL",
        "COMSPEC",
        "WSL_DISTRO_NAME",
        "SSH_CONNECTION",
        "SSH_TTY",
    ):
        monkeypatch.delenv(name, raising=False)

    facts = terminal_facts()

    assert facts["term"] is None
    assert facts["ssh"] is False
    assert "x" in facts["size"]


def test_install_facts_answer_the_bug_templates_checkbox() -> None:
    facts = install_facts()
    assert facts["installer"] in (None, "pip", "uv", "pipx", "hatch")
    assert isinstance(facts["in_venv"], bool)


def test_adapter_facts_report_what_an_adapter_declares() -> None:
    facts = adapter_facts("duckdb")

    assert list(facts) == ["duckdb"]
    assert facts["duckdb"]["error"] is None
    assert facts["duckdb"]["capabilities"]["implements_cancel"] is True


def test_adapter_facts_never_guess_false_about_an_adapter_that_will_not_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from harlequin.exception import HarlequinConfigError

    def _raise(name: str) -> None:
        raise HarlequinConfigError("boom")

    monkeypatch.setattr("harlequin.plugins.load_adapter", _raise)

    facts = adapter_facts("duckdb")

    assert facts["duckdb"]["capabilities"] == UNKNOWN
    assert facts["duckdb"]["error"] == "boom"
