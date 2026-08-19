"""`AbstractOption.to_dict()`: the serialization every non-rendering consumer reads.

The shape is the contract. `hsql --spec` publishes it, the generated config
schema is built from it, and third-party adapters subclass the classes that
produce it -- so what is asserted here is that the keys are the same for every
type, and that a subclass written before the method existed still answers.
"""

from __future__ import annotations

from typing import Any, Callable, Generator

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
