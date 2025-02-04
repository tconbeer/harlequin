from rich.panel import Panel


class HarlequinExit(Exception):
    pass


class HarlequinError(Exception):
    def __init__(self, msg: str, title: str = "") -> None:
        super().__init__(msg)
        self.msg = msg
        self.title = title


class HarlequinBindingError(HarlequinError):
    pass


class HarlequinConnectionError(HarlequinError):
    pass


class HarlequinCopyError(HarlequinError):
    pass


class HarlequinQueryError(HarlequinError):
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
    from rich import print as rich_print

    rich_print(pretty_error_message(error))


def pretty_error_message(error: HarlequinError) -> Panel:
    return Panel.fit(
        str(error),
        title=error.title if error.title else ("Harlequin encountered an error."),
        title_align="left",
        border_style="red",
    )


def pretty_print_warning(title: str, message: str) -> None:
    from rich import print as rich_print

    from harlequin.colors import GREEN

    rich_print(
        Panel.fit(
            message,
            title=title,
            title_align="left",
            border_style=GREEN,
        )
    )
