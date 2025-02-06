from __future__ import annotations

from typing import Type, Union

from textual.app import App, InvalidThemeError
from textual.binding import ActiveBinding
from textual.driver import Driver
from textual.screen import Screen
from textual.types import CSSPathType

from harlequin.colors import HARLEQUIN_TEXTUAL_THEME
from harlequin.exception import (
    HarlequinThemeError,
    pretty_error_message,
)


class ScreenBase(Screen):
    @property
    def active_bindings(self) -> dict[str, ActiveBinding]:
        def sort_key(binding_pair: tuple[str, ActiveBinding]) -> int:
            return 0 if binding_pair[1].node == self.app else 1

        binding_map = {
            k: v for k, v in sorted(super().active_bindings.items(), key=sort_key)
        }
        return binding_map


class AppBase(App, inherit_bindings=False):
    """
    A common base app for Harlequin and its mini-apps.
    """

    def __init__(
        self,
        *,
        theme: str | None = "harlequin",
        driver_class: Union[Type[Driver], None] = None,
        css_path: Union[CSSPathType, None] = None,
        watch_css: bool = False,
    ):
        super().__init__(driver_class, css_path, watch_css)
        self.register_theme(HARLEQUIN_TEXTUAL_THEME)
        try:
            self.theme = theme or "harlequin"
        except InvalidThemeError:
            from harlequin.colors import VALID_THEMES

            valid_themes = ", ".join(VALID_THEMES.keys())
            e = HarlequinThemeError(
                (
                    f"No theme found with the name {theme}.\n"
                    "Supported themes changed in Harlequin v2.0.0. "
                    "Theme must be `harlequin` or the name of a Textual Theme:\n"
                    f"{valid_themes}"
                ),
                title="Harlequin couldn't load your theme.",
            )
            self.exit(return_code=2, message=pretty_error_message(e))

    def get_default_screen(self) -> Screen:
        """
        Changes the default screen to re-order bindings, with global bindings first.
        """
        return ScreenBase(id="_default")

    def _handle_exception(self, error: Exception) -> None:
        """
        Prevents tracebacks from being printed due to exceptions that
        occur after App.exit() is called.
        See https://github.com/Textualize/textual/issues/5325
        """
        if self._exit:
            return
        return super()._handle_exception(error)
