"""The JSON Schema for a config file: what it accepts, and what it refuses.

A schema is only worth shipping if it is a schema, so most of what is here runs
a real validator over a real config document rather than asserting on the shape
of a dict. The two halves it has to get right are the same two the read path
does -- the keys a command owns, and the keys the adapter a profile names
declares -- and the interesting cases are all about the seam between them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import pytest
from jsonschema import Draft202012Validator

from harlequin.config import Config, adapter_options_model, sluggify_option_name
from harlequin.config_schema import SCHEMA_ID, build_schema
from harlequin.hsql.cli import bare_command
from harlequin.options import (
    AbstractOption,
    FlagOption,
    ListOption,
    SelectOption,
    TextOption,
)
from harlequin.plugins import adapter_names, load_adapter

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_SCHEMA_PATH = REPO_ROOT / "src" / "harlequin" / "schemas" / "config-v1.json"

FIX = "uv run python scripts/write_config_schema.py"
"""What to run when the packaged base schema and the generator disagree."""

FAKE_OPTIONS: list[AbstractOption] = [
    TextOption(name="host", description="Where the server is."),
    TextOption(name="port", description="Which port it answers on."),
    ListOption(name="extension", description="Load this extension. Repeatable."),
    SelectOption(
        name="sslmode",
        description="How to negotiate TLS.",
        choices=["disable", ("verify-full", "Verify the certificate")],
        default="disable",
    ),
    FlagOption(name="no-verify", description="Skip certificate verification."),
]
"""One of every option type an adapter can declare, including the two shapes a
`SelectOption`'s choices come in."""


def schema_for(
    adapters: dict[str, Sequence[AbstractOption] | None] | None,
) -> dict[str, Any]:
    """The schema for an installation, checked for being a schema at all."""
    schema = build_schema(bare_command().params, adapters)
    Draft202012Validator.check_schema(schema)
    return schema


def validator(
    adapters: dict[str, Sequence[AbstractOption] | None] | None,
) -> Draft202012Validator:
    return Draft202012Validator(schema_for(adapters))


def problems(check: Draft202012Validator, config: dict[str, Any]) -> list[str]:
    return [error.message for error in check.iter_errors(config)]


def profile(check: Draft202012Validator, **keys: Any) -> list[str]:
    return problems(check, {"profiles": {"p": keys}})


@pytest.fixture
def fake() -> Draft202012Validator:
    """An installation with one adapter, which declares one of everything."""
    return validator({"faux": FAKE_OPTIONS})


@pytest.fixture
def installed() -> Draft202012Validator:
    """This machine, adapters and all -- the document the mode writes."""
    return validator(
        {
            name: list(load_adapter(name).ADAPTER_OPTIONS or [])
            for name in adapter_names()
        }
    )


# --- the top level, which the model owns -------------------------------------


def test_every_key_the_model_names_is_described() -> None:
    """The schema and the parser have to agree on what a config file may say.

    Which they do by construction -- the top level comes from `Config` -- so
    this is what fails when a key added to the model needs a description.
    """
    described = schema_for({"faux": FAKE_OPTIONS})["properties"]
    assert set(described) == set(Config.__struct_fields__)
    assert all(described[key].get("description") for key in described)


def test_a_key_no_config_file_may_hold_is_an_error(fake: Draft202012Validator) -> None:
    assert problems(fake, {"profile": {}})


def test_a_keymap_takes_the_three_properties_a_binding_has(
    fake: Draft202012Validator,
) -> None:
    """The IDE refuses a fourth property by name, so the schema does too."""
    assert not problems(
        fake, {"keymaps": {"mine": [{"keys": "ctrl+j", "action": "quit"}]}}
    )
    assert problems(
        fake, {"keymaps": {"mine": [{"keys": "ctrl+j", "action": "quit", "hue": 1}]}}
    )
    assert problems(fake, {"keymaps": {"mine": [{"keys": "ctrl+j"}]}})


# --- a profile: the command's keys, and its adapter's -------------------------


def test_a_profile_takes_the_commands_own_options(fake: Draft202012Validator) -> None:
    assert not profile(fake, limit=1000, format="csv", tuples_only=True)


def test_a_profile_takes_the_options_of_the_adapter_it_names(
    fake: Draft202012Validator,
) -> None:
    assert not profile(
        fake,
        adapter="faux",
        host="db.example.com",
        extension=["postgis"],
        sslmode="verify-full",
        no_verify=True,
    )


def test_the_keys_described_are_the_fields_a_profile_is_parsed_into() -> None:
    """The schema and the validator read one declaration rather than two.

    Both go through `adapter_options_model()`, so an option the model drops --
    a name that is not an identifier -- is one the schema does not describe
    either, and a type the model learns arrives here without being taught.
    """
    options = {sluggify_option_name(o.name): o for o in FAKE_OPTIONS}
    described = schema_for({"faux": FAKE_OPTIONS})["$defs"]["faux_options"]
    assert set(described["properties"]) == set(
        adapter_options_model("faux", options).__struct_fields__
    )


def test_a_misspelled_option_is_an_error(fake: Draft202012Validator) -> None:
    """The whole reason to build the schema from the declarations.

    `reed_only` is dropped in silence by an adapter's constructor, so a schema
    that left a profile open would be a schema that could not catch the one
    typo that connects you read-write.
    """
    assert profile(fake, adapter="faux", reed_only=True)


def test_a_value_the_declared_choices_do_not_name_is_an_error(
    fake: Draft202012Validator,
) -> None:
    assert profile(fake, adapter="faux", sslmode="verify-ful")


def test_an_option_declared_as_text_takes_the_number_toml_invites(
    fake: Draft202012Validator,
) -> None:
    """`port = 5432` reaches the adapter as `"5432"`, so the schema takes it."""
    assert not profile(fake, adapter="faux", port=5432)
    assert not profile(fake, adapter="faux", port="5432")


def test_an_option_declared_as_a_list_has_to_be_written_as_one(
    fake: Draft202012Validator,
) -> None:
    assert profile(fake, adapter="faux", extension="postgis")


def test_a_profile_that_names_no_adapter_takes_the_default_adapters_options(
    installed: Draft202012Validator,
) -> None:
    """A profile with no `adapter` connects with duckdb, and takes its options."""
    assert not profile(installed, md_token="secret")
    assert profile(installed, md_toke="secret")


def test_one_adapters_options_are_not_anothers(
    installed: Draft202012Validator,
) -> None:
    assert not profile(installed, adapter="sqlite", timeout=3)
    assert profile(installed, adapter="duckdb", timeout=3)


def test_an_adapter_that_is_not_installed_is_an_error(
    fake: Draft202012Validator,
) -> None:
    """Which is what makes it this installation's schema rather than the format's."""
    assert profile(fake, adapter="duckdb")


def test_an_adapter_whose_options_could_not_be_read_leaves_a_profile_open() -> None:
    """An adapter that will not import is a reason to say nothing, not to refuse.

    Every key under a profile that names it would otherwise be reported as an
    error, on the strength of a list we could not read.
    """
    check = validator({"faux": FAKE_OPTIONS, "broken": None})
    assert not profile(check, adapter="broken", anything="at all")
    assert profile(check, adapter="faux", anything="at all")


def test_a_key_a_command_reads_is_not_read_as_an_adapters(
    fake: Draft202012Validator,
) -> None:
    """`limit` is hsql's wherever it appears, so it keeps hsql's type."""
    options = schema_for({"faux": FAKE_OPTIONS})["$defs"]["faux_options"]["properties"]
    assert "limit" not in options
    assert profile(fake, adapter="faux", limit=True)


def test_an_option_a_command_owns_is_described_once() -> None:
    """Both bundled adapters declare `read-only`, and hsql owns the spelling.

    So the key is described where the command's keys are, with the command's
    type, rather than twice under an adapter that would never be handed it.
    """
    profile_keys = schema_for(None)["$defs"]["profile"]["properties"]
    assert profile_keys["read_only"]["type"] == "boolean"
    for name in adapter_names():
        declared = load_adapter(name).ADAPTER_OPTIONS
        options = schema_for({name: declared})["$defs"][f"{name}_options"]
        assert "read_only" not in options["properties"]


def test_a_profile_takes_the_keys_the_ide_reads(fake: Draft202012Validator) -> None:
    """One profile serves both commands, so hsql's schema describes both."""
    assert not profile(fake, theme="nord", viewer_max_rows=10_000)


def test_conn_str_is_a_string_or_a_list_of_them(fake: Draft202012Validator) -> None:
    assert not profile(fake, conn_str="my.db")
    assert not profile(fake, conn_str=["my.db", "other.db"])


def test_a_row_count_written_as_a_boolean_is_an_error(
    fake: Draft202012Validator,
) -> None:
    """`limit = true` is not one row, and the read path already refuses it."""
    assert profile(fake, limit=True)


# --- the base schema, which describes the format rather than a machine --------


def test_the_base_schema_names_no_adapter() -> None:
    """Published for readers whose installations we know nothing about.

    So a profile stays open below the keys both commands read: the adapter it
    names may well be installed where the file is.
    """
    base = schema_for(None)
    assert not profile(Draft202012Validator(base), adapter="snowflake", account="acme")
    assert "enum" not in base["$defs"]["profile"]["properties"]["adapter"]
    assert base["$id"] == SCHEMA_ID


def test_the_base_schema_still_closes_what_it_does_know() -> None:
    check = validator(None)
    assert problems(check, {"profile": {}})
    assert profile(check, limit=True)


def test_only_the_base_schema_claims_the_published_id() -> None:
    """A schema built for one machine is not the one published at that URL."""
    assert "$id" not in schema_for({"faux": FAKE_OPTIONS})


def test_the_packaged_base_schema_is_what_the_generator_writes() -> None:
    """The file ships for the site to publish, and nothing reads it at run time.

    So this is the only thing that notices when the config model, hsql's
    options, or a description behind either of them has moved on without it.
    """
    packaged = json.loads(BASE_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert packaged == build_schema(bare_command().params, None), (
        f"the packaged base schema is out of date: {FIX}"
    )


def test_a_secret_option_is_write_only_and_carries_no_default() -> None:
    """JSON Schema's own word for a value that must not be shown back.

    An editor that knows the vocabulary does the right thing with the field
    without being told twice -- and a default an adapter shipped for a secret
    is a secret, so the schema does not write it down.
    """
    options: list[AbstractOption] = [
        TextOption(name="token", description="A token.", default="sh", secret=True),
        TextOption(name="host", description="Where.", default="localhost"),
    ]
    declared = schema_for({"faux": options})["$defs"]["faux_options"]["properties"]
    assert declared["token"]["writeOnly"] is True
    assert "default" not in declared["token"]
    assert "writeOnly" not in declared["host"]
    assert declared["host"]["default"] == "localhost"
