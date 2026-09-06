"""`AbstractOption.to_dict()`: the serialization every non-rendering consumer reads.

The shape is the contract. `hsql --spec` publishes it, the generated config
schema is built from it, and third-party adapters subclass the classes that
produce it -- so what is asserted here is that the keys are the same for every
type, and that a subclass written before the method existed still answers.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Generator, Iterator

import pytest

from harlequin.options import (
    AbstractOption,
    FlagOption,
    ListOption,
    PathOption,
    SelectOption,
    TextOption,
)

KEYS = {
    "name",
    "type",
    "label",
    "description",
    "short_decls",
    "default",
    "choices",
    "multiple",
    "secret",
}


class PrehistoricOption(AbstractOption):
    """An option written before `to_dict()`, `option_type` or any of this.

    It implements exactly the three renderings the ABC demanded when a
    third-party adapter would have written it, and nothing else. Whatever
    `to_dict()` does, it has to do it for this.
    """

    def merge(self, other: AbstractOption) -> AbstractOption:
        return self

    def to_click(self) -> Callable[..., Any]:  # pragma: no cover - never called
        raise NotImplementedError

    def to_widgets(self) -> Generator[Any, None, None]:  # pragma: no cover
        raise NotImplementedError

    def to_questionary(  # pragma: no cover - never called
        self, existing_value: Any | None = None
    ) -> Any:
        raise NotImplementedError


EVERY_OPTION = [
    TextOption(name="host", description="The host."),
    ListOption(name="extension", description="Load one."),
    PathOption(name="init-path", description="A file."),
    SelectOption(name="mode", description="How.", choices=["ro", "rw"]),
    FlagOption(name="read-only", description="Refuse writes."),
    PrehistoricOption(name="legacy", description="From before."),
]


@pytest.mark.parametrize("option", EVERY_OPTION, ids=lambda o: type(o).__name__)
def test_every_option_serializes_to_the_same_keys(option: AbstractOption) -> None:
    """A key that does not apply is null, not a key that is missing.

    A consumer that has to branch on whether a key is there is a consumer
    writing the three lines this could have written once.
    """
    assert set(option.to_dict()) == KEYS


def test_a_text_option_reports_what_it_was_declared_with() -> None:
    option = TextOption(
        name="host",
        description="The host to connect to.",
        label="Host",
        short_decls=["-h"],
        default="localhost",
    )
    assert option.to_dict() == {
        "name": "host",
        "type": "text",
        "label": "Host",
        "description": "The host to connect to.",
        "short_decls": ["-h"],
        "default": "localhost",
        "choices": None,
        "multiple": False,
        "secret": False,
    }


def test_a_short_decl_is_reported_the_way_it_will_be_typed() -> None:
    """`short_decls=["h"]` is accepted and stored as `-h`, and this reports the
    spelling, not the declaration."""
    option = TextOption(name="host", description="The host.", short_decls=["h"])
    assert option.to_dict()["short_decls"] == ["-h"]


def test_a_list_option_is_the_repeatable_one() -> None:
    option = ListOption(name="extension", description="Load an extension.")
    declared = option.to_dict()
    assert declared["type"] == "list"
    assert declared["multiple"] is True
    assert declared["default"] is None


def test_a_select_option_reports_the_values_a_caller_can_type() -> None:
    """A choice may be declared as a pair for a GUI to render, and a pair is not
    something anyone can type."""
    option = SelectOption(
        name="mode",
        description="Transaction mode.",
        choices=["auto", ("manual", "Manual")],
        default="auto",
    )
    declared = option.to_dict()
    assert declared["type"] == "select"
    assert declared["choices"] == ["auto", "manual"]
    assert declared["default"] == "auto"


def test_a_flag_reports_the_default_it_declared() -> None:
    """The option's own answer. What the *command line* does with a flag that
    was not passed is `--spec`'s translation, not this one's."""
    assert FlagOption(name="read-only", description="x").to_dict()["default"] is False
    assert (
        FlagOption(name="read-only", description="x", default=True).to_dict()["default"]
        is True
    )


def test_a_path_option_is_a_path() -> None:
    option = PathOption(name="init-path", description="A file.", default="~/.duckdbrc")
    assert option.to_dict()["type"] == "path"
    assert option.to_dict()["default"] == "~/.duckdbrc"


def test_an_option_that_predates_the_method_still_answers() -> None:
    """The reason `to_dict()` is concrete rather than abstract.

    `AbstractOption` is public API and third-party adapters subclass it, so an
    option class that has never heard of this method has to keep working: it
    has no `option_type` and no `default`, and neither may raise.
    """
    declared = PrehistoricOption(name="legacy", description="From before.").to_dict()
    assert declared["name"] == "legacy"
    assert declared["type"] == "prehistoric"
    assert declared["default"] is None
    assert declared["choices"] is None
    assert declared["multiple"] is False
    assert declared["secret"] is False


def test_a_subclass_of_a_declared_type_inherits_its_type() -> None:
    """An adapter that subclasses `TextOption` to add validation is still text,
    and a consumer switching on the type should see that rather than a name it
    has never met."""

    class HostOption(TextOption):
        pass

    assert HostOption(name="host", description="x").to_dict()["type"] == "text"


def test_the_in_tree_adapters_serialize() -> None:
    """Every option the bundled adapters declare, through the real classes."""
    from harlequin_duckdb import DUCKDB_OPTIONS
    from harlequin_sqlite import SQLITE_OPTIONS

    for option in [*DUCKDB_OPTIONS, *SQLITE_OPTIONS]:
        declared = option.to_dict()
        assert set(declared) == KEYS
        assert declared["name"]
        assert declared["type"] in ("text", "list", "path", "select", "flag")
        assert declared["secret"] in (True, False)


# --- `secret`, and what reads it ---------------------------------------------


def test_an_option_reports_what_it_declared_about_being_secret() -> None:
    """The declaration `harlequin.redact` and `--spec` both read.

    Core cannot enumerate every adapter's secret, so the adapter says so once
    and every consumer gets it from here.
    """
    assert TextOption(name="token", description="x", secret=True).to_dict()["secret"]
    assert not TextOption(name="host", description="x").to_dict()["secret"]


@pytest.mark.parametrize("option", EVERY_OPTION, ids=lambda o: type(o).__name__)
def test_nothing_is_secret_unless_it_says_so(option: AbstractOption) -> None:
    """False is the answer a subclass that predates the attribute has to give.

    A class attribute as well as a keyword, so that an option written before
    any of this answers rather than raising -- and answers the safe way round
    for the *caller*: an option core wrongly believed secret would be masked in
    every report, which is a bug a user would have to guess at.
    """
    assert option.secret is False
    assert option.to_dict()["secret"] is False


@pytest.mark.parametrize("adapter", ["duckdb", "sqlite"])
def test_every_declared_option_is_a_parameter_the_constructor_names(
    adapter: str,
) -> None:
    """The name a caller types and the name the adapter is handed are one name.

    An adapter takes supersets of what it declares and drops the rest, so a
    declaration whose name no parameter matches is an option that parses,
    validates, and does nothing -- in silence, which is how `--mode ro` opened
    a writable database.
    """
    import inspect

    from harlequin.config import sluggify_option_name
    from harlequin.plugins import load_adapter

    adapter_cls = load_adapter(adapter)
    declared = {
        sluggify_option_name(option.name)
        for option in adapter_cls.ADAPTER_OPTIONS or []
    }
    named = set(inspect.signature(adapter_cls.__init__).parameters)
    assert declared <= named


def test_the_duckdb_adapter_declares_its_token_secret() -> None:
    """The one secret in tree, and the reason the declaration is not theory."""
    from harlequin_duckdb import DUCKDB_OPTIONS

    declared = {option.name: option.to_dict() for option in DUCKDB_OPTIONS}
    assert declared["md_token"]["secret"] is True
    assert declared["md_saas"]["secret"] is False


@pytest.mark.parametrize(
    "left,right",
    [
        (
            TextOption(name="token", description="A token.", secret=True),
            TextOption(name="token", description="Not a token."),
        ),
        (
            TextOption(name="token", description="Not a token."),
            TextOption(name="token", description="A token.", secret=True),
        ),
    ],
    ids=["secret first", "secret second"],
)
def test_merging_two_options_keeps_the_secret_one_secret(
    left: AbstractOption, right: AbstractOption
) -> None:
    """Either half saying so is enough.

    Two adapters spelling the same option two ways is what `merge()` is for,
    and one of them calling it a password is the answer that cannot be wrong in
    the direction that hurts.
    """
    assert left.merge(right).to_dict()["secret"] is True


@pytest.mark.parametrize(
    "option_type", [TextOption, PathOption, ListOption], ids=lambda t: t.__name__
)
def test_a_secret_option_prompts_without_echoing(option_type: type) -> None:
    """The wizard asks the same question and does not write the answer down.

    A prompt that echoes a token puts it in a terminal, a screen share and a
    scrollback buffer at once, and the wizard is where a token is typed.
    """
    with _no_terminal():
        plain = option_type(name="token", description="x").to_questionary()
        secret = option_type(
            name="token", description="x", secret=True
        ).to_questionary()
    assert not _masks_input(plain)
    assert _masks_input(secret)


@contextmanager
def _no_terminal() -> Iterator[None]:
    """A prompt_toolkit session with nothing behind it.

    `to_questionary()` builds a real `PromptSession`, which reaches for the
    terminal as it is constructed -- and a test runner has none, which on
    Windows is an error rather than a fallback. So both ends are replaced: a
    pipe for input, and an output that draws nowhere.
    """
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            yield


@contextmanager
def _capture_prompt(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> Iterator[dict[str, Any]]:
    """Capture the kwargs of one questionary prompt kind, without a terminal.

    `to_questionary()` imports questionary when it is called, which resolves to
    the same module object -- so patching the attribute on the real module is
    enough, and no prompt session is ever built.
    """
    import questionary

    captured: dict[str, Any] = {}

    def fake_prompt(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(questionary, kind, fake_prompt)
    yield captured


def _masks_input(question: Any) -> bool:
    """Whether this prompt would show what is typed into it.

    Read off the real prompt rather than by watching which questionary function
    was called: what matters is that the input is not echoed, and that is a
    property of the object the wizard is about to run.
    """
    from prompt_toolkit.layout.processors import ConditionalProcessor, PasswordProcessor

    control = question.application.layout.current_window.content
    return any(
        isinstance(processor, ConditionalProcessor)
        and isinstance(processor.processor, PasswordProcessor)
        and processor.filter()
        for processor in control.input_processors
    )


# --- `to_questionary()`: what the wizard's prompts offer ---------------------


@pytest.mark.parametrize(
    ("option", "prompt_kind"),
    [
        (TextOption(name="host", description="The host."), "text"),
        (PathOption(name="init-path", description="A file."), "path"),
    ],
    ids=["text", "path"],
)
def test_an_unset_option_prompts_blank(
    monkeypatch: pytest.MonkeyPatch,
    option: AbstractOption,
    prompt_kind: str,
) -> None:
    """A profile that never set the option is not offered the string "None".

    The wizard passes `None` for an option the profile does not set, and
    `str(None)` would pre-fill "None" -- a value the user would save as text.
    """
    with _capture_prompt(monkeypatch, prompt_kind) as captured:
        option.to_questionary(None)
    assert captured["default"] == ""


def test_an_unset_secret_option_prompts_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The secret's default must be blank too: "None" renders as `****`."""
    with _capture_prompt(monkeypatch, "password") as captured:
        TextOption(name="token", description="A token.", secret=True).to_questionary(
            None
        )
    assert captured["default"] == ""


def test_an_unset_option_with_a_declared_default_offers_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset is not the same as blank for an option that declares a default."""
    with _capture_prompt(monkeypatch, "text") as captured:
        TextOption(
            name="host", description="The host.", default="localhost"
        ).to_questionary(None)
    assert captured["default"] == "localhost"


def test_an_unset_select_option_offers_its_declared_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _capture_prompt(monkeypatch, "select") as captured:
        SelectOption(
            name="mode", description="How.", choices=["ro", "rw"], default="ro"
        ).to_questionary(None)
    assert captured["default"] == "ro"


def test_an_existing_value_is_still_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the profile already says is what the prompt offers."""
    with _capture_prompt(monkeypatch, "text") as captured:
        TextOption(name="host", description="The host.").to_questionary(
            "db.example.com"
        )
    assert captured["default"] == "db.example.com"
