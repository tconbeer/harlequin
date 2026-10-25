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
    # Literal text unless this is exactly True. Driver messages quote
    # identifiers in brackets, and Rich would otherwise eat them as tags.
    # A subclass opts in by setting the class attribute -- the crash panel
    # does, because its message is already escaped and then linked. The
    # keyword overrides that for one instance. Adapters that never pass it
    # stay on the safe default.
    markup: bool = False

    def __init__(
        self, msg: str, title: str = "", *, markup: bool | None = None
    ) -> None:
        super().__init__(msg)
        self.msg = msg
        self.title = title
        if markup is not None:
            self.markup = markup


class HarlequinBindingError(HarlequinError):
    pass


class HarlequinCatalogPathError(HarlequinError):
    """A path into the catalog that cannot be read, or names nothing."""

    pass


class HarlequinConnectionError(HarlequinError):
    pass


class HarlequinCrashError(HarlequinError):
    """A bug in Harlequin itself, as the panel that replaces the traceback."""

    # `_as_markup` escapes the quoted exception, then wraps URLs in `[link=]`.
    markup = True


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
    from rich.text import Text

    raw_title = error.title if error.title else "Harlequin encountered an error."
    raw_body = str(error)
    # A subclass that replaced __init__ and never learned about `markup`
    # has no attribute, and stays literal. Only an exact True opts in, so a
    # truthy non-bool cannot turn driver text into tags.
    if getattr(error, "markup", False) is True:
        body: str | Text = raw_body
        title: str | Text = raw_title
    else:
        # Text, not an escaped string: Rich then cannot parse tags, emoji
        # codes, or links out of text we did not write.
        body = Text(raw_body)
        title = Text(raw_title)
    return Panel.fit(
        body,
        title=title,
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
