from __future__ import annotations

from typing import Any, Type, Union

from textual.app import App, InvalidThemeError
from textual.binding import ActiveBinding
from textual.driver import Driver
from textual.screen import Screen
from textual.types import CSSPathType

from harlequin.colors import HARLEQUIN_TEXTUAL_THEME
from harlequin.crash import build_crash_report, crash_message, write_crash_report
from harlequin.exception import (
    HarlequinCrashError,
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
        # before the theme below, which can call self.exit() and so reach the
        # handler that reads it
        self._crash_handled = False
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

    def _save_work_on_crash(self) -> bool:
        """Persist anything the user would lose. False if there was nothing to save."""
        return False

    def _crash_context(self) -> dict[str, Any]:
        """What this app was doing, for the crash report."""
        return {}

    def _handle_exception(self, error: Exception) -> None:
        """Write a crash report and print a panel, instead of a raw traceback.

        Textual renders an uncaught exception with `show_locals=True`, which
        puts connection strings, tokens and query results on the terminal, and
        tells the user nothing about what to do next.

        The order is by value: save the user's work first, report second,
        render last, each stage wrapped on its own. This runs inside an
        `except` block, so it must not be able to raise -- raising here is how
        a fix for crashes becomes the crash. `_exit` is Textual's own guard
        against exceptions raised after `exit()`
        (https://github.com/Textualize/textual/issues/5325); a second
        exception returns without reporting again.
        """
        if self._exit or self._crash_handled:
            return
        self._crash_handled = True

        saved = False
        try:
            saved = self._save_work_on_crash()
        except BaseException:
            pass

        report_path = None
        try:
            report_path = write_crash_report(
                build_crash_report(error, self._crash_context())
            )
        except BaseException:
            pass

        try:
            self.bell()
            # so the command that ran this app can forward a failure
            self._return_code = 1
            # `run_test` re-raises from these; a crashing functional test that
            # did not set them would silently pass
            if self._exception is None:
                self._exception = error
                self._exception_event.set()
            # panic() closes the message pump, so a modal is unreachable from
            # here anyway: this lands at _exit_renderables[0] and prints to
            # stderr once the terminal is restored
            self.panic(
                pretty_error_message(
                    HarlequinCrashError(
                        crash_message(report_path, error, saved),
                        title="Harlequin crashed.",
                    )
                )
            )
        except BaseException:
            # last resort: the user gets *something*
            super()._handle_exception(error)
            return

        # `textual run --dev`: append the full traceback for whoever is developing
        if "debug" in self.features:
            super()._handle_exception(error)
