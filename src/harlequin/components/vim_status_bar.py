"""
vim_status_bar.py

A small, self-contained status line for vim mode -- NORMAL / -- INSERT --
/ -- VISUAL -- / :command, the same idea as vim's own bottom line.

Kept as its own tiny widget (rather than reusing textual_textarea's
existing footer, which is already used for save/find/goto-line prompts)
so it can be always-visible without fighting that footer's show/hide
logic, and ships its own DEFAULT_CSS so no changes to Harlequin's main
.tcss file are needed.
"""

from __future__ import annotations

from textual.widgets import Static


class VimStatusBar(Static):
    DEFAULT_CSS = """
    VimStatusBar {
        height: 1;
        width: 100%;
        color: $primary;
        padding: 0 1;
    }
    
    VimStatusBar.hide {
        display: none;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
