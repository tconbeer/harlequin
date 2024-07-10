from __future__ import annotations

from typing import Type, Union

from textual.app import App
from textual.binding import ActiveBinding
from textual.css.stylesheet import Stylesheet
from textual.driver import Driver
from textual.screen import Screen
from textual.types import CSSPathType

from harlequin.colors import HarlequinColors
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
        try:
            theme = theme or "harlequin"
            self.app_colors = HarlequinColors.from_theme(theme)
        except HarlequinThemeError as e:
            self.exit(return_code=2, message=pretty_error_message(e))
        else:
            self.design = self.app_colors.design_system
            self.stylesheet = Stylesheet(variables=self.get_css_variables())

    def get_default_screen(self) -> Screen:
        """
        Changes the default screen to re-order bindings, with global bindings first.
        """
        return ScreenBase(id="_default")
