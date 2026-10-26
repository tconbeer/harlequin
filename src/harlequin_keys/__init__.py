"""Keymap plugins that sit beside the VS Code keymap and do not change it.

`files` is meant to be loaded after `vscode`. `vim` and `emacs` replace it:
each one is a full set of bindings, and its editor mode runs only while it
is the loaded keymap.
"""

from harlequin_keys.emacs import EMACS
from harlequin_keys.files import FILES
from harlequin_keys.vim import VIM

__all__ = ["EMACS", "FILES", "VIM"]
