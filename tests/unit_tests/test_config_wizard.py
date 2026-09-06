from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

import pytest

from harlequin.adapter import HarlequinAdapter, HarlequinConnection
from harlequin.config import load_config, load_profile
from harlequin.config_wizard import _wizard
from harlequin.options import FlagOption


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
    An optional `record` list collects every prompt's (message, kwargs).
    """
    import questionary

    def fake(
        answers: dict[str, Any],
        fallback: Callable[..., Any],
        record: list[tuple[str, dict[str, Any]]] | None,
    ) -> Callable[..., Any]:
        def prompt(message: str = "", **kwargs: Any) -> FakePrompt:
            if record is not None:
                record.append((message, kwargs))
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

    def yes(default: bool = True, **_: Any) -> bool:
        # the default it offered, which is what an unanswered prompt takes: a
        # confirm that offers none means yes, as questionary's does
        return bool(default)

    def run(
        config_path: Path,
        answers: dict[str, Any],
        record: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> None:
        fallbacks: tuple[tuple[str, Callable[..., Any]], ...] = (
            ("select", first_choice),
            ("text", the_default),
            ("path", the_default),
            # the prompt a secret option gets: same question, no echo
            ("password", the_default),
            ("checkbox", nothing_checked),
            ("confirm", yes),
        )
        for name, fallback in fallbacks:
            monkeypatch.setattr(questionary, name, fake(answers, fallback, record))
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


class TestSecrets:
    """What the wizard writes down, and what it puts on the screen.

    Two different things for a secret, and the difference is the point: the
    file needs the token to connect with, and the terminal -- shared, scrolled
    back, screenshotted -- needs never to have seen it.
    """

    SECRET = "hunter2-and-then-some"

    def test_a_secret_is_written_to_the_file_and_not_to_the_screen(
        self,
        tmp_path: Path,
        run_wizard: Callable[..., None],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\ntheme = "fruity"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "Which adapter": "duckdb",
                "adapter options": ["md_token"],
                "md_token": self.SECRET,
            },
        )

        # the file gets the value: a profile that connected with asterisks
        # would be a worse bug than the one this is about
        assert load_config(config_path=path).profiles["one"]["md_token"] == self.SECRET
        # the confirmation panel does not
        printed = capsys.readouterr().out
        assert self.SECRET not in printed
        assert "********" in printed

    def test_a_variable_the_file_reads_a_secret_from_is_written_back_unresolved(
        self,
        tmp_path: Path,
        run_wizard: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`${MYPASSWORD}` is what the author typed, and what they keep.

        The wizard reads a config file and writes it back, so a value resolved
        on the way in would be a plaintext password on the way out -- which is
        why interpolation happens on the read path and not in `ConfigFile`.
        """
        monkeypatch.setenv("MYPASSWORD", self.SECRET)
        path = tmp_path / ".harlequin.toml"
        path.write_text(
            '[profiles.one]\nmd_token = "${MYPASSWORD}"\ntheme = "fruity"\n'
        )

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "Which adapter": "duckdb",
                # checked, and its value left at what the file already says
                "adapter options": ["md_token"],
            },
        )

        written = path.read_text()
        assert "${MYPASSWORD}" in written
        assert self.SECRET not in written
        # and the run path still resolves it
        assert (
            load_profile(config_path=path, profile_name="one")["md_token"]
            == self.SECRET
        )


class TestReadOnly:
    """The prompt for the one core option that decides what a session may do."""

    def test_saying_yes_writes_the_key(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\ntheme = "fruity"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "connect read-only": True,
            },
        )

        assert load_config(config_path=path).profiles["one"]["read_only"] is True

    def test_a_profile_that_is_not_read_only_says_nothing(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        """Read-write is the default, so the key is a line that says nothing."""
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\ntheme = "fruity"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "connect read-only": False,
            },
        )

        assert "read_only" not in load_config(config_path=path).profiles["one"]

    def test_saying_no_turns_off_a_profile_that_was_read_only(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        """Turning it off has to reach the file, or it did not happen."""
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nread_only = true\ntheme = "fruity"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "connect read-only": False,
            },
        )

        assert "read_only" not in load_config(config_path=path).profiles["one"]

    def test_the_question_is_not_put_to_an_adapter_that_cannot_answer_it(
        self,
        tmp_path: Path,
        run_wizard: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A profile that said yes is one Harlequin would refuse to start under.

        Answered yes here, so a question that was put would have written the
        key -- and the profile that had it loses it, which is the same answer
        `--config validate` gives about the file it was already in.
        """

        class Undeclared(HarlequinAdapter):
            ADAPTER_OPTIONS = None

            def __init__(self, conn_str: Sequence[str], **options: Any) -> None:
                raise NotImplementedError

            def connect(self) -> HarlequinConnection:
                raise NotImplementedError

        monkeypatch.setattr(
            "harlequin.config_wizard.load_adapter_plugins",
            lambda: {"undeclared": Undeclared},
        )
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "undeclared"\nread_only = true\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "connect read-only": True,
            },
        )

        assert "read_only" not in load_config(config_path=path).profiles["one"]

    def test_the_prompt_offers_what_the_profile_already_says(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        """Unanswered, it takes the default -- so a read-only profile rewritten
        without touching this question is still read-only."""
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nread_only = true\ntheme = "fruity"\n')

        run_wizard(path, {"Which profile would you like to update?": "one"})

        assert load_config(config_path=path).profiles["one"]["read_only"] is True


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


class TestDefaults:
    """Prompts that offered a value the user did not ask for (#1105).

    The wizard's job is to write what a user typed, not to guess: a fresh
    profile gets no `limit`, and an adapter option that stays blank (or gets
    cleared) is not written at all.
    """

    def test_a_fresh_profile_is_not_given_a_limit(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "duckdb"\n')

        run_wizard(path, {"Which profile would you like to update?": "one"})

        assert "limit" not in load_config(config_path=path).profiles["one"]

    def test_an_existing_limit_is_offered_and_kept(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "duckdb"\nlimit = 500\n')

        run_wizard(path, {"Which profile would you like to update?": "one"})

        assert load_config(config_path=path).profiles["one"]["limit"] == 500

    def test_blank_removes_an_existing_limit(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "duckdb"\nlimit = 500\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "How many rows should each query fetch": "",
            },
        )

        assert "limit" not in load_config(config_path=path).profiles["one"]

    def test_a_typed_limit_is_written(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "duckdb"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "How many rows should each query fetch": "1000",
            },
        )

        assert load_config(config_path=path).profiles["one"]["limit"] == 1000

    def test_minus_one_is_accepted_and_omitted(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        """`-1` still means no limit, which is the app default: no key."""
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "duckdb"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "How many rows should each query fetch": "-1",
            },
        )

        assert "limit" not in load_config(config_path=path).profiles["one"]

    def test_the_limit_prompt_offers_blank_and_says_so(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "duckdb"\n')
        record: list[tuple[str, dict[str, Any]]] = []

        run_wizard(
            path, {"Which profile would you like to update?": "one"}, record=record
        )

        limit_prompt = next(
            kwargs
            for message, kwargs in record
            if "How many rows should each query fetch" in message
        )
        assert limit_prompt["default"] == ""
        assert (
            limit_prompt["instruction"]
            == "Leave blank for app defaults; enter -1 for no limit."
        )
        validate = limit_prompt["validate"]
        assert validate("") is True
        assert validate("1000") is True
        assert validate("abc") is False

    def test_a_blank_secret_option_is_not_written(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        """The `****` a secret's `None` used to be shown as never reaches the file."""
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "duckdb"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "adapter options": ["md_token"],
                "md_token": "",
            },
        )

        assert "md_token" not in load_config(config_path=path).profiles["one"]

    def test_a_blank_text_option_is_not_written(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "duckdb"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "adapter options": ["custom-extension-repo"],
                "custom-extension-repo": "",
            },
        )

        assert (
            "custom_extension_repo" not in load_config(config_path=path).profiles["one"]
        )

    def test_a_blank_list_option_is_not_written(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        """Blank must not become a list that holds one empty item."""
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "duckdb"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "adapter options": ["extension"],
                "extension": "",
            },
        )

        assert "extension" not in load_config(config_path=path).profiles["one"]

    def test_blank_clears_an_existing_adapter_option(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "duckdb"\nmd_token = "hunter2"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "adapter options": ["md_token"],
                "md_token": "",
            },
        )

        assert "md_token" not in load_config(config_path=path).profiles["one"]

    def test_an_explicit_false_on_a_flag_is_written(
        self,
        tmp_path: Path,
        run_wizard: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A flag whose declared default is true needs `false` stored to override it."""

        class FakeAdapter(HarlequinAdapter):
            ADAPTER_OPTIONS = [
                FlagOption(name="legacy-mode", description="x", default=True)
            ]

            def __init__(self, conn_str: Sequence[str], **options: Any) -> None:
                raise NotImplementedError

            def connect(self) -> HarlequinConnection:
                raise NotImplementedError

        monkeypatch.setattr(
            "harlequin.config_wizard.load_adapter_plugins",
            lambda: {"fake": FakeAdapter},
        )
        path = tmp_path / ".harlequin.toml"
        path.write_text('[profiles.one]\nadapter = "fake"\n')

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "adapter options": ["legacy-mode"],
                "legacy-mode": False,
            },
        )

        assert load_config(config_path=path).profiles["one"]["legacy_mode"] is False


class TestSsh:
    """The tunnel's keys, which only a profile that has one should carry."""

    PROFILE = '[profiles.one]\nadapter = "duckdb"\n'

    def test_saying_no_writes_no_ssh_keys(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text(self.PROFILE)

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "Do you connect via SSH?": False,
            },
        )

        profile = load_config(config_path=path).profiles["one"]
        assert not [key for key in profile if key.startswith("ssh")]

    def test_the_question_defaults_to_no(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        """An unanswered confirm takes the default it offered."""
        path = tmp_path / ".harlequin.toml"
        path.write_text(self.PROFILE)
        record: list[tuple[str, dict[str, Any]]] = []

        run_wizard(
            path, {"Which profile would you like to update?": "one"}, record=record
        )

        asked = [kwargs for message, kwargs in record if "via SSH" in message]
        assert asked and asked[0]["default"] is False
        profile = load_config(config_path=path).profiles["one"]
        assert not [key for key in profile if key.startswith("ssh")]

    def test_saying_yes_writes_the_tunnel(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text(self.PROFILE)

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "Do you connect via SSH?": True,
                "What SSH destination": "redshift_prod",
                "What should it forward?": "15439:data.example.com:5439",
                "Should ssh fail rather than prompt": True,
                "How many seconds should Harlequin wait": "30",
            },
        )

        profile = load_config(config_path=path).profiles["one"]
        assert profile["ssh_host"] == "redshift_prod"
        assert profile["ssh_forward"] == ["15439:data.example.com:5439"]
        assert profile["ssh_batch_mode"] is True
        assert profile["ssh_timeout"] == 30

    def test_a_forward_the_ssh_config_supplies_is_left_out(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        """The motivating case: the Host block has the LocalForward."""
        path = tmp_path / ".harlequin.toml"
        path.write_text(self.PROFILE)

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "Do you connect via SSH?": True,
                "What SSH destination": "redshift_prod",
                "What should it forward?": "",
                "Should ssh fail rather than prompt": False,
                "How many seconds should Harlequin wait": "",
            },
        )

        profile = load_config(config_path=path).profiles["one"]
        assert profile["ssh_host"] == "redshift_prod"
        assert "ssh_forward" not in profile
        assert "ssh_batch_mode" not in profile
        assert "ssh_timeout" not in profile

    def test_several_forwards_are_written_as_a_list(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text(self.PROFILE)

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "Do you connect via SSH?": True,
                "What SSH destination": "web-1",
                "What should it forward?": "15439:one:5439 15440:two:5440",
            },
        )

        assert load_config(config_path=path).profiles["one"]["ssh_forward"] == [
            "15439:one:5439",
            "15440:two:5440",
        ]

    def test_an_existing_tunnel_is_offered_back(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text(
            "[profiles.one]\n"
            'adapter = "duckdb"\n'
            'ssh_host = "redshift_prod"\n'
            'ssh_forward = ["15439:data.example.com:5439"]\n'
        )
        record: list[tuple[str, dict[str, Any]]] = []

        run_wizard(
            path, {"Which profile would you like to update?": "one"}, record=record
        )

        offered = {
            message: kwargs.get("default")
            for message, kwargs in record
            if "via SSH" in message or "SSH destination" in message
        }
        assert offered["Do you connect via SSH?"] is True
        assert "redshift_prod" in str(offered)
        profile = load_config(config_path=path).profiles["one"]
        assert profile["ssh_host"] == "redshift_prod"
        assert profile["ssh_forward"] == ["15439:data.example.com:5439"]

    def test_the_wizard_never_writes_the_key_a_config_file_may_not_answer(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        """`ssh_allow_reuse` is read from the command line only."""
        path = tmp_path / ".harlequin.toml"
        path.write_text(self.PROFILE)
        record: list[tuple[str, dict[str, Any]]] = []

        run_wizard(
            path,
            {
                "Which profile would you like to update?": "one",
                "Do you connect via SSH?": True,
                "What SSH destination": "web-1",
            },
            record=record,
        )

        assert "reuse" not in " ".join(message for message, _ in record).lower()
        assert "ssh_allow_reuse" not in load_config(config_path=path).profiles["one"]

    def test_the_tunnel_is_asked_about_before_the_adapter_options(
        self, tmp_path: Path, run_wizard: Callable[..., None]
    ) -> None:
        path = tmp_path / ".harlequin.toml"
        path.write_text(self.PROFILE)
        record: list[tuple[str, dict[str, Any]]] = []

        run_wizard(
            path, {"Which profile would you like to update?": "one"}, record=record
        )

        messages = [message for message, _ in record]
        asked = next(i for i, m in enumerate(messages) if "via SSH" in m)
        options = next(i for i, m in enumerate(messages) if "adapter options" in m)
        assert asked < options
