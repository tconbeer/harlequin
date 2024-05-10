from __future__ import annotations

import sys
from typing import Literal, overload

from harlequin.adapter import HarlequinAdapter
from harlequin.keymap import HarlequinKeyMap

if sys.version_info < (3, 10):
    from importlib_metadata import entry_points
else:
    from importlib.metadata import entry_points


def load_adapter_plugins() -> dict[str, type[HarlequinAdapter]]:
    return _load_plugins(group="harlequin.adapter")


def load_keymap_plugins() -> dict[str, HarlequinKeyMap]:
    return _load_plugins(group="harlequin.keymap")


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
