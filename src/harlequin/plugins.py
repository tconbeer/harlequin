from __future__ import annotations

import sys
from typing import Dict

from harlequin.adapter import HarlequinAdapter

if sys.version_info < (3, 10):
    from importlib_metadata import entry_points
else:
    from importlib.metadata import entry_points


def load_plugins() -> Dict[str, type[HarlequinAdapter]]:
    adapter_eps = entry_points(group="harlequin.adapter")
    adapters: Dict[str, type[HarlequinAdapter]] = {}

    for ep in adapter_eps:
        try:
            adapters.update({ep.name: ep.load()})
        except ImportError as e:
            print(
                f"Harlequin could not load the installed plug-in named {ep.name}."
                f"\n\n{e}"
            )

    return adapters
