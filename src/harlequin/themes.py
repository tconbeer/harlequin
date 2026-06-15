from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from platformdirs import user_config_path
from textual.color import Color
from textual.theme import Theme as TextualTheme
from tomlkit.exceptions import TOMLKitError
from tomlkit.toml_file import TOMLFile

from harlequin.exception import HarlequinThemeError

# Fields recognized in a theme TOML file; unknown keys are silently ignored
# so theme files can include author/description metadata without erroring.
_THEME_FIELDS = frozenset(
    {
        "primary",
        "secondary",
        "background",
        "surface",
        "panel",
        "warning",
        "error",
        "success",
        "accent",
        "foreground",
        "dark",
    }
)


@dataclass
class HarlequinTheme:
    """Represents a user-defined Harlequin color theme loaded from a TOML file."""

    name: str
    primary: str
    secondary: str | None = None
    background: str | None = None
    surface: str | None = None
    panel: str | None = None
    warning: str | None = None
    error: str | None = None
    success: str | None = None
    accent: str | None = None
    foreground: str | None = None
    dark: bool = True

    def to_textual_theme(self) -> TextualTheme:
        color_fields: dict[str, str | None] = {
            "primary": self.primary,
            "secondary": self.secondary,
            "background": self.background,
            "surface": self.surface,
            "panel": self.panel,
            "warning": self.warning,
            "error": self.error,
            "success": self.success,
            "accent": self.accent,
            "foreground": self.foreground,
        }
        for color in color_fields.values():
            if color is not None:
                Color.parse(color)

        return TextualTheme(
            name=self.name,
            dark=self.dark,
            **{k: v for k, v in color_fields.items() if v is not None},  # type: ignore[arg-type]
        )


class UserThemeLoadResult(NamedTuple):
    loaded_themes: dict[str, TextualTheme]
    failed_themes: list[tuple[Path, Exception]]


def get_theme_directory() -> Path:
    """Return the platform-appropriate directory for user theme files.

    On Linux:   ~/.config/harlequin/themes/
    On macOS:   ~/Library/Application Support/harlequin/themes/
    On Windows: C:\\Users\\<user>\\AppData\\Local\\harlequin\\themes\\
    """
    return user_config_path(appname="harlequin", appauthor=False) / "themes"


def load_user_theme(path: Path) -> TextualTheme:
    """Parse a single TOML theme file and return a TextualTheme.

    Raises HarlequinThemeError if the file cannot be parsed or contains
    invalid color values.
    """
    toml_file = TOMLFile(path)
    try:
        raw: dict = toml_file.read().unwrap()
    except TOMLKitError as e:
        raise HarlequinThemeError(
            f"Could not parse theme file at {path}: {e}",
            title="Harlequin couldn't load your theme.",
        ) from e

    name = str(raw.get("name", path.stem))
    known = {k: v for k, v in raw.items() if k in _THEME_FIELDS}
    try:
        theme = HarlequinTheme(name=name, **known)
        return theme.to_textual_theme()
    except Exception as e:
        raise HarlequinThemeError(
            f"Invalid theme file at {path}: {e}",
            title="Harlequin couldn't load your theme.",
        ) from e


def load_user_themes(theme_dir: Path | None = None) -> UserThemeLoadResult:
    """Load all TOML theme files from the theme directory.

    Returns a UserThemeLoadResult with successfully loaded themes and a list
    of (path, exception) pairs for any files that failed to load. A missing
    theme directory is not an error — it returns empty results.
    """
    directory = theme_dir if theme_dir is not None else get_theme_directory()
    loaded: dict[str, TextualTheme] = {}
    failed: list[tuple[Path, Exception]] = []

    if not directory.exists():
        return UserThemeLoadResult(loaded_themes=loaded, failed_themes=failed)

    for path in directory.iterdir():
        if path.suffix == ".toml":
            try:
                theme = load_user_theme(path)
                loaded[theme.name] = theme
            except Exception as e:
                failed.append((path, e))

    return UserThemeLoadResult(loaded_themes=loaded, failed_themes=failed)
