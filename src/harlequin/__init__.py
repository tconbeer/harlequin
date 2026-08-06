"""Harlequin's public API.

Every name below is exported lazily (PEP 562). Importing this package -- which
happens implicitly when importing *any* `harlequin.*` submodule -- used to pull
in `harlequin.app`, and with it Textual, sqlfmt, prompt_toolkit and pyarrow. That
put a ~660ms floor under every consumer, including adapters that never touch the
TUI. Attribute access still resolves the same names from the same modules, so
`from harlequin import Harlequin` works exactly as before; it just pays for the
app only when the app is what you asked for.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from harlequin.adapter import (
        HarlequinAdapter,
        HarlequinConnection,
        HarlequinCursor,
    )
    from harlequin.app import Harlequin
    from harlequin.autocomplete import HarlequinCompletion
    from harlequin.keymap import HarlequinKeyBinding, HarlequinKeyMap
    from harlequin.keys_app import HarlequinKeys
    from harlequin.options import HarlequinAdapterOption, HarlequinCopyFormat
    from harlequin.transaction_mode import HarlequinTransactionMode

__all__ = [
    "Harlequin",
    "HarlequinAdapter",
    "HarlequinAdapterOption",
    "HarlequinCompletion",
    "HarlequinConnection",
    "HarlequinCopyFormat",
    "HarlequinCursor",
    "HarlequinTransactionMode",
    "HarlequinKeys",
    "HarlequinKeyMap",
    "HarlequinKeyBinding",
]

_LAZY_ATTRS = {
    "Harlequin": "harlequin.app",
    "HarlequinAdapter": "harlequin.adapter",
    "HarlequinAdapterOption": "harlequin.options",
    "HarlequinCompletion": "harlequin.autocomplete",
    "HarlequinConnection": "harlequin.adapter",
    "HarlequinCopyFormat": "harlequin.options",
    "HarlequinCursor": "harlequin.adapter",
    "HarlequinTransactionMode": "harlequin.transaction_mode",
    "HarlequinKeys": "harlequin.keys_app",
    "HarlequinKeyMap": "harlequin.keymap",
    "HarlequinKeyBinding": "harlequin.keymap",
}


def __getattr__(name: str) -> Any:
    try:
        module_name = _LAZY_ATTRS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    from importlib import import_module

    value = getattr(import_module(module_name), name)
    # cache on the module so subsequent lookups skip __getattr__ entirely
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
