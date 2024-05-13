from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from typing_extensions import NotRequired

from harlequin.exception import HarlequinConfigError

# TODO: ADD VALIDATION when creating bindings from config


class RawKeyBinding(TypedDict):
    keys: str
    action: str
    key_display: NotRequired[str | None]


@dataclass
class HarlequinKeyBinding:
    keys: str
    """Comma-separated list of virtual key names."""
    action: str
    """The name of an action. Must be a key of harlequin.actions.HARLEQUIN_ACTIONS"""
    key_display: str | None = None
    """If specified, overrides the key display in Harlequin footer for this binding."""


@dataclass
class HarlequinKeyMap:
    name: str
    bindings: list[HarlequinKeyBinding]

    @classmethod
    def from_config(cls, name: str, bindings: list[RawKeyBinding]) -> "HarlequinKeyMap":
        try:
            keymap = cls(
                name=name,
                bindings=[HarlequinKeyBinding(**binding) for binding in bindings],
            )
        except TypeError as e:
            bad_key = str(e).split("argument ")[-1].strip("'")
            raise HarlequinConfigError(
                title="Harlequin could not load your keymap.",
                msg=(
                    "Key bindings must be defined in config files with "
                    "only three properties: `keys`, `action`, and `key_profile`. "
                    f"Got a binding in the map named {name} that tried to define "
                    f"a property: {bad_key!r}"
                ),
            ) from e
        return keymap