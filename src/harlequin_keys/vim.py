"""The VS Code bindings, the file shortcuts, and a vim mode in the editor.

Insert mode is where a query starts, so typing still inserts text and the
author's editor shortcuts still run. Escape enters normal mode: hjkl, w, b,
counts, and a space leader for the rest of the app. Those letters are not
bindings, so they insert text again the moment insert mode returns.
"""

from harlequin.keymap import HarlequinKeyMap
from harlequin_keys.files import FILES
from harlequin_vscode import (
    VSCODE_APP_BINDINGS,
    VSCODE_DATA_CATALOG_BINDINGS,
    VSCODE_EDITOR_BINDINGS,
    VSCODE_HISTORY_SCREEN_BINDINGS,
    VSCODE_RESULTS_VIEWER_BINDINGS,
)

VIM = HarlequinKeyMap(
    name="vim",
    input_mode="vim",
    bindings=[
        *VSCODE_APP_BINDINGS,
        *VSCODE_EDITOR_BINDINGS,
        *VSCODE_DATA_CATALOG_BINDINGS,
        *VSCODE_RESULTS_VIEWER_BINDINGS,
        *VSCODE_HISTORY_SCREEN_BINDINGS,
        *FILES.bindings,
    ],
)
