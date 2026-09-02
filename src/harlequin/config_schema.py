"""The JSON Schema for a config file, from the config model and what is installed.

A schema is the third thing to say about a config file, beside reading it and
validating it, and it is the one an editor and an agent can both use: the keys a
profile may hold, what each is for, and which values it takes. `Config` is
already the declaration the read path validates against, so the top level comes
from `msgspec.json.schema()` and cannot drift from what a file is parsed into.

**The keys below the top level come from the same two sources validation uses.**
A profile holds a command's own options -- click parameters -- and the
connection options of the adapter it names, which are
`adapter_options_model()`: the struct `parse_profile_options()` parses a profile
into, built from what that adapter declares. So the types here are the types a
run enforces, rather than a second reading of the same declarations, and
`AbstractOption.to_dict()` is left to supply what a struct has no room for --
each option's description and its default.

**Which makes it an installation's schema, not the format's.** It knows which
adapters are installed here and what each of them takes, so `adapter` is an enum
of those names and a key none of them declares is an error -- which is the
point, since that key is one a run would have refused. A caller that wants the
format rather than the installation takes `schemas/config-v1.json`, the same
document built for no adapters at all: open below a profile, because any adapter
may be installed where it is read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Container, Mapping, Sequence

import msgspec

from harlequin.config import (
    CLI_ONLY_SSH_KEYS,
    DEFAULT_ADAPTER,
    TUI_ONLY_KEYS,
    Config,
    adapter_options_model,
    sluggify_option_name,
)
from harlequin.keymap import RawKeyBinding

if TYPE_CHECKING:
    import click

    from harlequin.options import AbstractOption

DIALECT = "https://json-schema.org/draft/2020-12/schema"
"""2020-12 for `unevaluatedProperties`, which is what lets a profile be closed
and still take the keys an `if`/`then` branch adds for its adapter."""

SCHEMA_ID = "https://harlequin.sh/schemas/config/v1.json"
"""Where the base schema is published, and so the `$id` only it carries."""

TITLE = "Harlequin config"

DESCRIPTION = (
    "A .harlequin.toml or harlequin.toml file, or the [tool.harlequin] table of "
    "a pyproject.toml. Read by both the harlequin IDE and the hsql command."
)

DESCRIPTIONS = {
    "default_profile": (
        "The profile both commands load when -P/--profile does not name one."
    ),
    "profiles": (
        "Named sets of options, one table each. -P NAME loads one; the profile "
        "named None is Harlequin's own defaults."
    ),
    "keymaps": (
        "Named key bindings for the harlequin IDE, one array of bindings each."
    ),
    "profile": (
        "A profile's options: the ones each command reads for itself, plus the "
        "connection options declared by the adapter it names."
    ),
    "keybinding": (
        "One key binding: the keys, the action they run, and how to show them."
    ),
    "conn_str": (
        "What to connect to, which both commands take positionally. One string, "
        "or an array of them for an adapter that takes several."
    ),
}
"""Written here for the keys nothing else describes: the three top-level tables,
and the one profile key click has no help text for because it is an argument."""

IDE_ONLY = "Read by the harlequin IDE; hsql ignores it. See `harlequin --help`."

UNKNOWN_ADAPTER_OPTIONS = (
    "This adapter is installed but could not be imported, so the options it "
    "declares are unknown and any key is allowed here."
)

CLICK_TYPES: dict[str, dict[str, Any]] = {
    "boolean": {"type": "boolean"},
    "choice": {"type": "string"},
    "file": {"type": "string"},
    "float": {"type": "number"},
    "float range": {"type": "number"},
    "integer": {"type": "integer"},
    "integer range": {"type": "integer"},
    "path": {"type": "string"},
    "text": {"type": "string"},
}
"""A click parameter's type, as a JSON Schema one. A type in neither this nor
`click.Choice` is left unconstrained rather than guessed at."""

TEXT = ["string", "number"]
"""What an option the model declares as a string accepts, which is both: TOML
invites `port = 5432`, and the read path turns it into the string the adapter
declared."""


def build_schema(
    command_options: Sequence[click.Parameter],
    adapters: Mapping[str, Sequence[AbstractOption] | None] | None,
) -> dict[str, Any]:
    """The JSON Schema for a config file, given a command's options and an
    installation's adapters.

    `adapters` maps each installed adapter to the options it declares, or to
    None where they could not be read. Pass None for the whole mapping to
    describe the file format rather than an installation: no adapter is named,
    and a profile stays open to the keys an adapter that is not here would take.
    """
    document: dict[str, Any] = {"$schema": DIALECT}
    if adapters is None:
        document["$id"] = SCHEMA_ID
    document.update(_top_level())
    profile = _profile(command_options, adapters)
    document["$defs"] = {
        "profile": profile,
        "keybinding": _keybinding(),
        **{
            _options_key(name): _adapter_options(
                name, declared, owned=set(profile["properties"])
            )
            for name, declared in sorted((adapters or {}).items())
        },
    }
    return document


def _top_level() -> dict[str, Any]:
    """What a config file may say at the top, from the model that parses it.

    Through `msgspec.json.schema()`, so a key added to `Config` appears here
    without being written twice -- with the two tables pointed at the
    definitions below, which the model holds as plain objects.
    """
    schema: dict[str, Any] = msgspec.json.schema(Config)["$defs"]["Config"]
    schema["title"] = TITLE
    schema["description"] = DESCRIPTION
    properties = schema["properties"]
    properties["default_profile"]["description"] = DESCRIPTIONS["default_profile"]
    properties["profiles"].update(
        description=DESCRIPTIONS["profiles"],
        additionalProperties={"$ref": "#/$defs/profile"},
    )
    properties["keymaps"].update(
        description=DESCRIPTIONS["keymaps"],
        additionalProperties={
            "type": "array",
            "items": {"$ref": "#/$defs/keybinding"},
        },
    )
    return schema


def _keybinding() -> dict[str, Any]:
    """One binding in a keymap, from the TypedDict the IDE reads them into."""
    schema: dict[str, Any] = msgspec.json.schema(RawKeyBinding)["$defs"][
        "RawKeyBinding"
    ]
    schema["title"] = "Key binding"
    schema["description"] = DESCRIPTIONS["keybinding"]
    # `HarlequinKeyMap.from_config()` refuses a fourth property by name, so the
    # schema says so too
    schema["additionalProperties"] = False
    return schema


def _profile(
    command_options: Sequence[click.Parameter],
    adapters: Mapping[str, Sequence[AbstractOption] | None] | None,
) -> dict[str, Any]:
    """One `[profiles.x]` table: the command's keys, and its adapter's.

    Which keys an adapter contributes depends on the value of `adapter`, so they
    arrive as an `if`/`then` branch each and the table is closed with
    `unevaluatedProperties` -- the one spelling that counts a branch's keys as
    described. Left open where no installation is described, because the adapter
    a profile names may be installed on the machine the file is read on.
    """
    properties = {
        param.name: _from_parameter(param)
        for param in command_options
        if param.name is not None
    }
    for key in TUI_ONLY_KEYS:
        properties.setdefault(key, {"description": IDE_ONLY})
    for key in CLI_ONLY_SSH_KEYS:
        # refused from a profile at run time, so a file that sets one is wrong
        # however the editor validating it was told to read this document
        properties.pop(key, None)

    schema: dict[str, Any] = {
        "type": "object",
        "title": "Profile",
        "description": DESCRIPTIONS["profile"],
        "properties": properties,
    }
    # the one property of this document that only an installation can answer,
    # and the same source the branches below are built from
    if adapters is None:
        properties.get("adapter", {}).pop("enum", None)
        return schema
    if "adapter" in properties:
        properties["adapter"]["enum"] = sorted(adapters)

    schema["allOf"] = [
        {
            "if": {"properties": {"adapter": {"const": name}}, "required": ["adapter"]},
            "then": {"$ref": f"#/$defs/{_options_key(name)}"},
        }
        for name in sorted(adapters)
    ]
    if DEFAULT_ADAPTER in adapters:
        # a profile that names no adapter connects with the default one, and so
        # takes its options
        schema["allOf"].append(
            {
                "if": {"not": {"required": ["adapter"]}},
                "then": {"$ref": f"#/$defs/{_options_key(DEFAULT_ADAPTER)}"},
            }
        )
    schema["unevaluatedProperties"] = False
    return schema


def _adapter_options(
    name: str, declared: Sequence[AbstractOption] | None, *, owned: Container[str]
) -> dict[str, Any]:
    """One adapter's connection options, as the keys a profile writes them under.

    `owned` names the keys a command reads for itself, which never reach an
    adapter however it declares them.
    """
    if declared is None:
        # nothing is known about this adapter's options, so nothing under a
        # profile that names it can be called an error
        return {
            "title": f"{name} options",
            "description": UNKNOWN_ADAPTER_OPTIONS,
            "unevaluatedProperties": True,
        }
    # a name a command reads is that command's own, whoever else declares it:
    # `parse_profile_options()` takes those keys off before the model sees them
    options = {
        key: option
        for option in declared
        if (key := sluggify_option_name(option.name)) not in owned
    }
    model = adapter_options_model(name, options)
    # its `properties` and nothing else: the model is closed because it sees an
    # adapter's keys and no others, where the profile this describes part of
    # holds the command's too. Closing that one is `unevaluatedProperties`.
    properties = msgspec.json.schema(model)["$defs"][model.__name__]["properties"]
    return {
        "title": f"{name} options",
        "description": f"The connection options the {name} adapter declares.",
        "properties": {
            key: _from_option(schema, options[key])
            for key, schema in properties.items()
        },
    }


def _from_parameter(param: click.Parameter) -> dict[str, Any]:
    """One of a command's own options, as the key a profile writes it under."""
    schema: dict[str, Any] = dict(CLICK_TYPES.get(param.type.name, {}))
    if choices := getattr(param.type, "choices", None):
        schema["enum"] = [str(choice) for choice in choices]
    if getattr(param, "multiple", False) or param.nargs == -1:
        # both spellings reach the command as a sequence, and both commands take
        # a lone string for one of them
        schema = {"anyOf": [schema, {"type": "array", "items": dict(schema)}]}
    description = getattr(param, "help", None) or DESCRIPTIONS.get(str(param.name))
    if description is not None:
        schema["description"] = description
    # `to_info_dict()` rather than `default`, because click derives a flag's
    # default rather than storing one, and that is the method that resolves it
    default = param.to_info_dict()["default"]
    if isinstance(default, (str, int, float, bool)):
        # anything else is click's sentinel for a parameter declared without a
        # default, or a callable one that would read the environment
        schema["default"] = default
    return schema


def _from_option(schema: Mapping[str, Any], option: AbstractOption) -> dict[str, Any]:
    """One field of the model, plus what the option says about itself.

    The model spells "a profile need not set this" as a type that also takes
    null, which TOML cannot write: the schema says the same thing by leaving the
    key out of `required`, so the null goes.
    """
    (described,) = [
        variant for variant in schema["anyOf"] if variant != {"type": "null"}
    ]
    described = dict(described)
    if described.get("type") == "string":
        # `_as_declared()` rounds a number toward the string its option declared
        # before the model sees it, so `port = 5432` is a value a run takes
        described["type"] = list(TEXT)
    declared = option.to_dict()
    if declared["description"]:
        described["description"] = declared["description"]
    if isinstance(declared["default"], (str, int, float, bool)):
        described["default"] = declared["default"]
    if declared["secret"]:
        # JSON Schema uses "write only" to flag secrets
        described["writeOnly"] = True
        # a default that is a secret is still a secret
        described.pop("default", None)
    return described


def _options_key(adapter: str) -> str:
    """Where one adapter's options live under `$defs`."""
    return f"{adapter}_options"
