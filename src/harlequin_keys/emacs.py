"""Emacs bindings for the query editor, and the rest of the VS Code map.

`ctrl+p` moves up while the editor is focused, and `alt+x` opens the command
palette from anywhere. `ctrl+x` is a prefix, handled by the emacs mode, for
save, quit, open, and the buffer commands. Load this keymap on its own.
"""

from harlequin.keymap import HarlequinKeyBinding, HarlequinKeyMap
from harlequin_keys.files import FILES
from harlequin_vscode import (
    VSCODE_APP_BINDINGS,
    VSCODE_DATA_CATALOG_BINDINGS,
    VSCODE_EDITOR_BINDINGS,
    VSCODE_HISTORY_SCREEN_BINDINGS,
    VSCODE_RESULTS_VIEWER_BINDINGS,
)

# The editor actions whose VS Code key an Emacs user expects to mean something
# else. The replacement is appended below; a missing action is left as it was.
_EDITOR_KEYS = {
    "code_editor.select_all": None,
    "code_editor.find": "ctrl+s",
    "code_editor.save_buffer": None,
    "code_editor.new_buffer": None,
    "code_editor.next_buffer": "ctrl+pagedown",
    "code_editor.close_buffer": None,
    "code_editor.cursor_left": "left,ctrl+b",
    "code_editor.cursor_right": "right,ctrl+f",
    "code_editor.cursor_up": "up,ctrl+p",
    "code_editor.cursor_down": "down,ctrl+n",
    "code_editor.cursor_line_start": "home,ctrl+a",
    "code_editor.cursor_line_end": "end,ctrl+e",
    "code_editor.cursor_word_left": "ctrl+left,alt+b",
    "code_editor.cursor_word_right": "ctrl+right,alt+f",
    "code_editor.delete_right": "delete,ctrl+d",
    "code_editor.delete_to_end_of_line": "ctrl+k",
    "code_editor.paste": "ctrl+y,shift+insert",
    "code_editor.cut": "ctrl+w",
    "code_editor.redo": "ctrl+shift+z",
    "code_editor.undo": "ctrl+z,ctrl+slash",
    "code_editor.goto_line": "alt+g",
}


def _editor_bindings() -> list[HarlequinKeyBinding]:
    bindings: list[HarlequinKeyBinding] = []
    for binding in VSCODE_EDITOR_BINDINGS:
        if binding.action not in _EDITOR_KEYS:
            bindings.append(binding)
            continue
        keys = _EDITOR_KEYS[binding.action]
        if keys is None:
            continue
        bindings.append(
            HarlequinKeyBinding(keys, binding.action, key_display=binding.key_display)
        )
    bindings.append(HarlequinKeyBinding("alt+d", "code_editor.delete_word_right"))
    bindings.append(
        HarlequinKeyBinding("alt+backspace", "code_editor.delete_word_left")
    )
    bindings.append(HarlequinKeyBinding("ctrl+pageup", "code_editor.previous_buffer"))
    return bindings


def _app_bindings() -> list[HarlequinKeyBinding]:
    """The VS Code app keys, plus alt+x for the palette Emacs calls M-x."""
    bindings: list[HarlequinKeyBinding] = []
    for binding in VSCODE_APP_BINDINGS:
        if binding.action == "command_palette":
            bindings.append(HarlequinKeyBinding("ctrl+p,alt+x", "command_palette"))
            continue
        bindings.append(binding)
    return bindings


EMACS = HarlequinKeyMap(
    name="emacs",
    input_mode="emacs",
    bindings=[
        *_app_bindings(),
        *_editor_bindings(),
        *VSCODE_DATA_CATALOG_BINDINGS,
        *VSCODE_RESULTS_VIEWER_BINDINGS,
        *VSCODE_HISTORY_SCREEN_BINDINGS,
        *FILES.bindings,
    ],
)
