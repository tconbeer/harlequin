from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

import pytest

from harlequin.config import load_config
from harlequin.config_wizard import _wizard


class FakePrompt:
    """One questionary prompt, answered without a terminal."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer

    def ask(self) -> Any:
        return self.answer

    def unsafe_ask(self) -> Any:
        return self.answer


@pytest.fixture
def run_wizard(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Run the real wizard, with every prompt answered from a dict.

    Answers are keyed by a substring of the prompt's message; a prompt no test
    answers takes what the wizard offered it -- the value already in the
    profile, or the first choice -- so a test names only the prompts it is about.
    """
    import questionary

    def fake(
        answers: dict[str, Any], fallback: Callable[..., Any]
    ) -> Callable[..., Any]:
        def prompt(message: str = "", **kwargs: Any) -> FakePrompt:
            for substring, answer in answers.items():
                if substring in message:
                    return FakePrompt(answer)
            return FakePrompt(fallback(**kwargs))

        return prompt

    def first_choice(choices: Sequence[Any], **_: Any) -> Any:
        return choices[0]

    def the_default(default: Any = "", **_: Any) -> Any:
        return default

    def nothing_checked(**_: Any) -> list[Any]:
        return []

    def yes(**_: Any) -> bool:
        return True

    def run(config_path: Path, answers: dict[str, Any]) -> None:
        fallbacks: tuple[tuple[str, Callable[..., Any]], ...] = (
            ("select", first_choice),
            ("text", the_default),
            ("path", the_default),
            ("checkbox", nothing_checked),
            ("confirm", yes),
        )
        for name, fallback in fallbacks:
            monkeypatch.setattr(questionary, name, fake(answers, fallback))
        _wizard(config_path=config_path)

    return run


class TestDefaultProfile:
    def test_choosing_no_default_removes_it_from_the_file(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        """Turning a default off has to reach the file, or it did not happen.

        `[No default]` is the first choice the prompt offers, which is what the
        unanswered prompts take -- so this names it by leaving it unanswered.
        """
        path = tmp_path / ".harlequin.toml"
        path.write_text(
            'default_profile = "one"\n\n[profiles.one]\n# keep me\ntheme = "fruity"\n'
        )

        run_wizard(path, {"Which profile would you like to update?": "one"})

        assert load_config(config_path=path).default_profile is None
        written = path.read_text()
        assert "default_profile" not in written
        # and the write is still a write that keeps the file someone typed
        assert "# keep me" in written

    def test_setting_a_default_writes_it(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\n# keep me\ntheme = "fruity"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "Would you like to set a default profile?": "one",
            },
        )

        assert load_config(config_path=path).default_profile == "one"
        assert "# keep me" in path.read_text()


class TestProfileWrite:
    def test_the_wizard_keeps_the_comments_in_a_profile_it_did_not_touch(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text(
            'default_profile = "one"\n'
            "\n"
            "[profiles.one]\n"
            "# I like this theme best\n"
            'theme = "fruity"\n'
            "# the database I use every day\n"
            'conn_str = ["analytics.db"]\n'
        )

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "[Create a New Profile]",
                "What would you like to name your profile?": "two",
                "Would you like to set a default profile?": "one",
            },
        )

        written = path.read_text()
        assert "# I like this theme best" in written
        assert "# the database I use every day" in written
        config = load_config(config_path=path)
        assert config.profiles["one"] == {
            "theme": "fruity",
            "conn_str": ["analytics.db"],
        }
        assert "two" in config.profiles
        assert config.default_profile == "one"
