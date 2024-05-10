from __future__ import annotations

from dataclasses import dataclass


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
