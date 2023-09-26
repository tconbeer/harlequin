from typing import Dict, Union

from pygments.styles import get_style_by_name
from pygments.token import Token
from pygments.util import ClassNotFound
from textual.design import ColorSystem

from harlequin.exception import HarlequinThemeError


def extract_color(s: str) -> str:
    for part in s.split(" "):
        if part.startswith("#"):
            return part
    else:
        return ""


class HarlequinColorSystem(ColorSystem):
    def __init__(
        self,
        primary: str,
        secondary: Union[str, None] = None,
        warning: Union[str, None] = None,
        error: Union[str, None] = None,
        success: Union[str, None] = None,
        accent: Union[str, None] = None,
        background: Union[str, None] = None,
        surface: Union[str, None] = None,
        panel: Union[str, None] = None,
        boost: Union[str, None] = None,
        dark: bool = False,
        luminosity_spread: float = 0.15,
        text_alpha: float = 0.95,
        text: Union[str, None] = None,
    ):
        super().__init__(
            primary,
            secondary,
            warning,
            error,
            success,
            accent,
            background,
            surface,
            panel,
            boost,
            dark,
            luminosity_spread,
            text_alpha,
        )
        self.text = text

    def generate(self) -> Dict[str, str]:
        colors = super().generate()
        if self.text:
            colors["text"] = self.text
        return colors


class HarlequinColors:
    MAPPING = {
        "text": [Token, Token.Name, Token.Name.Variable],
        "primary": [Token.Keyword, Token],
        "secondary": [
            Token.Name.Function,
            Token.Name.Class,
            Token.Literal.String,
            Token,
        ],
        "gray": [Token.Comment, Token],
        "error": [Token.Error, Token.Name.Exception, Token],
    }
    FALLBACK = {
        "text": "#000000",
        "primary": "#000000",
        "secondary": "#000000",
        "gray": "#888888",
        "error": "#FF0000",
    }

    def __init__(
        self,
        background: str,
        highlight: str,
        text: str,
        primary: str,
        secondary: str,
        gray: str,
        error: str,
    ) -> None:
        self.background = background
        self.highlight = highlight
        self.text = text
        self.primary = primary
        self.secondary = secondary
        self.gray = gray
        self.error = error

    @classmethod
    def from_theme(cls, theme: str) -> "HarlequinColors":
        try:
            style = get_style_by_name(theme)
        except ClassNotFound as e:
            raise HarlequinThemeError(theme) from e

        background = style.background_color
        highlight = style.highlight_color
        style_colors = {
            prop_name: [
                extract_color(style.styles.get(token_type, ""))
                for token_type in token_type_list
                if extract_color(style.styles.get(token_type, ""))
            ]
            for prop_name, token_type_list in cls.MAPPING.items()
        }
        best_colors = {k: v[0] for k, v in style_colors.items() if v}
        best_colors.update(
            {k: cls.FALLBACK[k] for k, v in style_colors.items() if not v}
        )
        return cls(background, highlight, **best_colors)

    @property
    def color_system(self) -> ColorSystem:
        return ColorSystem(
            primary=self.primary,
            secondary=self.secondary,
            error=self.error,
            background=self.background,
            surface=self.background,
            boost=self.highlight,
            panel=self.gray,
        )

    @property
    def design_system(self) -> Dict[str, ColorSystem]:
        return {
            "dark": self.color_system,
            "light": self.color_system,
        }
