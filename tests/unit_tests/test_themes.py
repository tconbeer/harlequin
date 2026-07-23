from __future__ import annotations

from pathlib import Path

import pytest
from textual.theme import Theme as TextualTheme

from harlequin.exception import HarlequinThemeError
from harlequin.themes import (
    HarlequinTheme,
    UserThemeLoadResult,
    load_user_theme,
    load_user_themes,
)


def write_theme(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


class TestHarlequinTheme:
    def test_to_textual_theme_minimal(self) -> None:
        theme = HarlequinTheme(name="minimal", primary="#ff0000")
        result = theme.to_textual_theme()
        assert isinstance(result, TextualTheme)
        assert result.name == "minimal"

    def test_to_textual_theme_all_fields(self) -> None:
        theme = HarlequinTheme(
            name="full",
            primary="#4e78c4",
            secondary="#f39c12",
            accent="#e74c3c",
            background="#0e1726",
            surface="#17202a",
            panel="#1e2a35",
            error="#e74c3c",
            success="#2ecc71",
            warning="#f1c40f",
            foreground="#dddddd",
            dark=True,
        )
        result = theme.to_textual_theme()
        assert isinstance(result, TextualTheme)
        assert result.name == "full"

    def test_to_textual_theme_light(self) -> None:
        theme = HarlequinTheme(name="light", primary="#2c4251", dark=False)
        result = theme.to_textual_theme()
        assert result.dark is False

    def test_to_textual_theme_invalid_color_raises(self) -> None:
        from textual.color import ColorParseError

        theme = HarlequinTheme(name="bad", primary="not-a-color")
        with pytest.raises(ColorParseError):
            theme.to_textual_theme()


class TestLoadUserTheme:
    def test_valid_minimal_theme(self, tmp_path: Path) -> None:
        path = write_theme(
            tmp_path / "my-theme.toml",
            'name = "my-theme"\nprimary = "#4e78c4"\n',
        )
        result = load_user_theme(path)
        assert isinstance(result, TextualTheme)
        assert result.name == "my-theme"

    def test_valid_full_theme(self, tmp_path: Path) -> None:
        path = write_theme(
            tmp_path / "full.toml",
            "\n".join(
                [
                    'name = "full"',
                    'primary = "#4e78c4"',
                    'secondary = "#f39c12"',
                    'accent = "#e74c3c"',
                    'background = "#0e1726"',
                    'surface = "#17202a"',
                    'panel = "#1e2a35"',
                    'error = "#e74c3c"',
                    'success = "#2ecc71"',
                    'warning = "#f1c40f"',
                    "dark = true",
                ]
            ),
        )
        result = load_user_theme(path)
        assert result.name == "full"

    def test_name_defaults_to_file_stem(self, tmp_path: Path) -> None:
        path = write_theme(
            tmp_path / "fallback-name.toml",
            'primary = "#4e78c4"\n',
        )
        result = load_user_theme(path)
        assert result.name == "fallback-name"

    def test_unknown_fields_are_ignored(self, tmp_path: Path) -> None:
        path = write_theme(
            tmp_path / "meta.toml",
            "\n".join(
                [
                    'name = "meta"',
                    'primary = "#4e78c4"',
                    'author = "Someone"',
                    'description = "A theme"',
                    'homepage = "https://example.com"',
                ]
            ),
        )
        result = load_user_theme(path)
        assert result.name == "meta"

    def test_invalid_color_raises(self, tmp_path: Path) -> None:
        path = write_theme(
            tmp_path / "bad.toml",
            'name = "bad"\nprimary = "not-a-color"\n',
        )
        with pytest.raises(HarlequinThemeError, match="Invalid theme"):
            load_user_theme(path)

    def test_invalid_toml_raises(self, tmp_path: Path) -> None:
        path = write_theme(
            tmp_path / "bad.toml",
            "name = [unclosed\n",
        )
        with pytest.raises(HarlequinThemeError, match="Could not parse"):
            load_user_theme(path)

    def test_missing_primary_raises(self, tmp_path: Path) -> None:
        path = write_theme(
            tmp_path / "no-primary.toml",
            'name = "no-primary"\nsecondary = "#f39c12"\n',
        )
        with pytest.raises(HarlequinThemeError):
            load_user_theme(path)

    def test_light_theme(self, tmp_path: Path) -> None:
        path = write_theme(
            tmp_path / "light.toml",
            'name = "light"\nprimary = "#2c4251"\ndark = false\n',
        )
        result = load_user_theme(path)
        assert result.dark is False


class TestLoadUserThemes:
    def test_empty_directory(self, tmp_path: Path) -> None:
        result = load_user_themes(theme_dir=tmp_path)
        assert isinstance(result, UserThemeLoadResult)
        assert result.loaded_themes == {}
        assert result.failed_themes == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        result = load_user_themes(theme_dir=tmp_path / "nonexistent")
        assert result.loaded_themes == {}
        assert result.failed_themes == []

    def test_ignores_non_toml_files(self, tmp_path: Path) -> None:
        (tmp_path / "theme.yaml").write_text(
            'name = "yaml"\nprimary = "#4e78c4"\n', encoding="utf-8"
        )
        (tmp_path / "theme.json").write_text(
            '{"name": "json"}', encoding="utf-8"
        )
        (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
        result = load_user_themes(theme_dir=tmp_path)
        assert result.loaded_themes == {}
        assert result.failed_themes == []

    def test_loads_multiple_themes(self, tmp_path: Path) -> None:
        for i, color in enumerate(["#ff0000", "#00ff00", "#0000ff"]):
            write_theme(
                tmp_path / f"theme-{i}.toml",
                f'name = "theme-{i}"\nprimary = "{color}"\n',
            )
        result = load_user_themes(theme_dir=tmp_path)
        assert len(result.loaded_themes) == 3
        assert result.failed_themes == []

    def test_failed_themes_tracked_separately(self, tmp_path: Path) -> None:
        write_theme(
            tmp_path / "good.toml",
            'name = "good"\nprimary = "#4e78c4"\n',
        )
        write_theme(
            tmp_path / "bad.toml",
            'name = "bad"\nprimary = "not-a-color"\n',
        )
        result = load_user_themes(theme_dir=tmp_path)
        assert len(result.loaded_themes) == 1
        assert "good" in result.loaded_themes
        assert len(result.failed_themes) == 1
        assert result.failed_themes[0][0] == tmp_path / "bad.toml"

    def test_returns_themes_keyed_by_name(self, tmp_path: Path) -> None:
        write_theme(
            tmp_path / "filename.toml",
            'name = "custom-name"\nprimary = "#4e78c4"\n',
        )
        result = load_user_themes(theme_dir=tmp_path)
        assert "custom-name" in result.loaded_themes
        assert "filename" not in result.loaded_themes
