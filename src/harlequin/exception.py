from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.panel import Panel

# Every headless path imports this module -- `harlequin.config` does, so a
# console script pays for it before it has parsed a flag -- and rich is 20ms
# that only the two functions at the bottom actually need.


class HarlequinExit(Exception):
    pass


class HarlequinError(Exception):
    def __init__(self, msg: str, title: str = "") -> None:
        super().__init__(msg)
        self.msg = msg
        self.title = title


class HarlequinBindingError(HarlequinError):
    pass


class HarlequinCatalogPathError(HarlequinError):
    """A path into the catalog that cannot be read, or names nothing."""

    pass


class HarlequinConnectionError(HarlequinError):
    pass


class HarlequinCopyError(HarlequinError):
    pass


class HarlequinExternalError(HarlequinError):
    """Someone else's program that Harlequin could not run, or could not read."""

    pass


class HarlequinQueryError(HarlequinError):
    pass


class HarlequinSshError(HarlequinError):
    """An `ssh` child that would not start, or would not open its forwards."""

    pass


class HarlequinThemeError(HarlequinError):
    pass


class HarlequinConfigError(HarlequinError):
    pass


class HarlequinWizardError(HarlequinError):
    pass


class HarlequinTzDataError(HarlequinError):
    pass


class HarlequinLocaleError(HarlequinError):
    pass


def pretty_print_error(error: HarlequinError) -> None:
    from rich.console import Console

    # errors are diagnostics on a failing exit path, so they belong on stderr;
    # rich.print would put them on stdout and contaminate piped output.
    Console(stderr=True).print(pretty_error_message(error))


def pretty_error_message(error: HarlequinError) -> "Panel":
    from rich.panel import Panel

    return Panel.fit(
        str(error),
        title=error.title if error.title else ("Harlequin encountered an error."),
        title_align="left",
        border_style="red",
    )


def pretty_print_warning(title: str, message: str) -> None:
    from rich.console import Console
    from rich.panel import Panel

    from harlequin.colors import GREEN

    # Warnings go to stderr for the same reason errors do: rich.print would put
    # them on stdout and contaminate piped output.
    Console(stderr=True).print(
        Panel.fit(
            message,
            title=title,
            title_align="left",
            border_style=GREEN,
        )
    )
