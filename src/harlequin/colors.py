from __future__ import annotations

import os

from questionary import Style as QuestionaryStyle
from textual.theme import BUILTIN_THEMES
from textual.theme import Theme as TextualTheme

GREEN = "#45FFCA"
YELLOW = "#FEFFAC"
PINK = "#FFB6D9"
PURPLE = "#D67BFF"
GRAY = "#777777"
DARK_GRAY = "#555555"
BLACK = "#0C0C0C"
WHITE = "#DDDDDD"

HARLEQUIN_TEXTUAL_THEME = TextualTheme(
    name="harlequin",
    primary=YELLOW,
    secondary=GREEN,
    warning=YELLOW,
    error=PINK,
    success=GREEN,
    accent=PINK,
    foreground=WHITE,
    background=BLACK,
    surface=BLACK,
    panel=DARK_GRAY,
    dark=True,
)

VALID_THEMES = BUILTIN_THEMES
VALID_THEMES.pop("textual-ansi")
VALID_THEMES.update({"harlequin": HARLEQUIN_TEXTUAL_THEME})

HARLEQUIN_QUESTIONARY_STYLE = (
    QuestionaryStyle(
        [
            ("qmark", "fg:ansidefault bold"),
            ("question", "fg:ansidefault nobold"),
            ("answer", "fg:ansidefault bold"),
            ("pointer", "fg:ansidefault bold"),
            ("highlighted", "fg:ansidefault bold"),
            ("selected", "fg:ansidefault noreverse bold"),
            ("separator", "fg:ansidefault"),
            ("instruction", "fg:ansidefault italic"),
            ("text", ""),
            ("disabled", "fg:ansidefault italic"),
        ]
    )
    if os.getenv("NO_COLOR")
    else QuestionaryStyle(
        [
            ("qmark", f"fg:{GREEN} bold"),
            ("question", "bold"),
            ("answer", f"fg:{YELLOW} bold"),
            ("pointer", f"fg:{YELLOW} bold"),
            ("highlighted", f"fg:{YELLOW} bold"),
            ("selected", f"fg:{YELLOW} noreverse bold"),
            ("separator", f"fg:{PURPLE}"),
            ("instruction", "fg:#858585 italic"),
            ("text", ""),
            ("disabled", "fg:#858585 italic"),
        ]
    )
)
