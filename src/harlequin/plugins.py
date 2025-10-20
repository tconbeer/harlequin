from __future__ import annotations

from importlib.metadata import entry_points
from typing import Literal, Sequence, overload

from harlequin.adapter import HarlequinAdapter
from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyMap


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
    plugins: dict[str, HarlequinKeyMap] | dict[str, type[HarlequinAdapter]] = {}
    for ep in eps:
        try:
            ep_class = ep.load()
        except ImportError as e:
            print(
                f"Harlequin could not load the installed plug-in named {e.name}.\n\n{e}"
            )
        else:
            plugins[ep.name] = ep_class
    return plugins
