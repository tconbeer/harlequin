from __future__ import annotations

import sys
from typing import Literal, Sequence, overload

from harlequin.adapter import HarlequinAdapter
from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyMap

if sys.version_info < (3, 10):
    from importlib_metadata import entry_points
else:
    from importlib.metadata import entry_points


def load_adapter_plugins() -> dict[str, type[HarlequinAdapter]]:
    return _load_plugins(group="harlequin.adapter")


def load_keymap_plugins(
    user_defined_keymaps: Sequence[HarlequinKeyMap],
) -> dict[str, HarlequinKeyMap]:
    keymaps = _load_plugins(group="harlequin.keymap")
    for keymap in user_defined_keymaps:
        if keymap.name in keymaps:
            raise HarlequinConfigError(
                title="Harlequin could not load your keymap config",
                msg=(
                    "Your Harlequin config files define a keymap named "
                    f"{keymap.name}, but that name is already defined by "
                    "a plug-in keymap. To extend a plug-in keymap, define "
                    "a keymap with a new name, and configure your profile to "
                    "load both keymaps."
                ),
            )
        keymaps[keymap.name] = keymap
    return keymaps


@overload
def _load_plugins(
    group: Literal["harlequin.adapter"],
) -> dict[str, type[HarlequinAdapter]]: ...


@overload
def _load_plugins(group: Literal["harlequin.keymap"]) -> dict[str, HarlequinKeyMap]: ...


def _load_plugins(
    group: str,
) -> dict[str, HarlequinKeyMap] | dict[str, type[HarlequinAdapter]]:
    eps = entry_points(group=group)
    try:
        plugins: dict[str, HarlequinKeyMap] | dict[str, type[HarlequinAdapter]] = {
            ep.name: ep.load() for ep in eps
        }
    except ImportError as e:
        print(
            f"Harlequin could not load the installed plug-in named {e.name}." f"\n\n{e}"
        )
    return plugins
