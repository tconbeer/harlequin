from __future__ import annotations

import sys
from importlib.metadata import entry_points
from typing import Literal, Sequence, cast, overload

from harlequin.adapter import HarlequinAdapter
from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyMap


def adapter_names() -> list[str]:
    """
    The name of every installed adapter, without importing any of them.
    """
    return sorted({ep.name for ep in entry_points(group="harlequin.adapter")})


def load_adapter(name: str) -> type[HarlequinAdapter]:
    """
    Import exactly one installed adapter, by its entry point name.

    Unlike load_adapter_plugins(), a failure here is fatal: the caller asked for
    this adapter by name, so there is nothing to fall back to.

    Raises: HarlequinConfigError if no installed plug-in registers that name, or
    if the adapter it registers cannot be imported.
    """
    matches = [ep for ep in entry_points(group="harlequin.adapter") if ep.name == name]
    if not matches:
        installed = ", ".join(adapter_names())
        raise HarlequinConfigError(
            f"Could not load an adapter named {name}, because no installed "
            "plug-in provides one with that name. Installed adapters: "
            f"{installed if installed else '(none)'}.",
            title="Harlequin could not load your adapter.",
        )
    ep = matches[-1]  # last one wins, to agree with load_adapter_plugins()
    try:
        return cast("type[HarlequinAdapter]", ep.load())
    except ImportError as e:
        raise HarlequinConfigError(
            f"Could not load the installed plug-in named {ep.name}.\n\n{e}",
            title="Harlequin could not load your adapter.",
        ) from e


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
            # stderr, not stdout: a plug-in failing to load must not end up in
            # piped output.
            print(
                f"Harlequin could not load the installed plug-in named "
                f"{e.name}.\n\n{e}",
                file=sys.stderr,
            )
        else:
            plugins[ep.name] = ep_class
    return plugins
