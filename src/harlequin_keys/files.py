"""Shortcuts for the actions the VS Code keymap leaves to the command palette.

None of these keys are in that keymap, on the same widget. Load this one
after it: `--keymap-name vscode --keymap-name files`.
"""

from harlequin.keymap import HarlequinKeyBinding, HarlequinKeyMap

FILES = HarlequinKeyMap(
    name="files",
    bindings=[
        HarlequinKeyBinding("ctrl+shift+e", "show_files"),
        HarlequinKeyBinding("ctrl+shift+o", "open_folder"),
        HarlequinKeyBinding("ctrl+alt+o", "open_file"),
        HarlequinKeyBinding("ctrl+shift+w", "close_folder"),
        HarlequinKeyBinding("ctrl+alt+n", "new_file"),
        HarlequinKeyBinding("ctrl+alt+shift+n", "new_folder"),
        HarlequinKeyBinding("ctrl+alt+r", "rename_path"),
        HarlequinKeyBinding("ctrl+alt+d", "delete_path"),
        HarlequinKeyBinding("ctrl+alt+s", "show_s3"),
        HarlequinKeyBinding("ctrl+pageup", "code_editor.previous_buffer"),
        HarlequinKeyBinding("ctrl+pagedown", "code_editor.next_buffer"),
        HarlequinKeyBinding("ctrl+shift+k", "code_editor.close_all_buffers"),
        HarlequinKeyBinding("ctrl+alt+t", "toggle_transaction_mode"),
        HarlequinKeyBinding("ctrl+alt+enter", "commit_transaction"),
        HarlequinKeyBinding("ctrl+alt+backspace", "rollback_transaction"),
        HarlequinKeyBinding("ctrl+alt+c", "cancel_query"),
        HarlequinKeyBinding("ctrl+alt+comma", "change_theme"),
        HarlequinKeyBinding("alt+1", "focus_query_editor"),
        HarlequinKeyBinding("alt+2", "focus_results_viewer"),
        HarlequinKeyBinding("alt+3", "focus_data_catalog"),
        HarlequinKeyBinding("alt+4", "show_query_history"),
        HarlequinKeyBinding("alt+5", "show_data_exporter"),
        HarlequinKeyBinding("alt+0", "toggle_sidebar"),
    ],
)
