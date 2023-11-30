class HarlequinExit(Exception):
    pass


class HarlequinError(Exception):
    def __init__(self, msg: str, title: str = "") -> None:
        super().__init__(msg)
        self.msg = msg
        self.title = title


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


def pretty_print_error(error: HarlequinError) -> None:
    from rich import print
    from rich.panel import Panel

    print(
        Panel.fit(
            str(error),
            title=error.title if error.title else ("Harlequin encountered an error."),
            title_align="left",
            border_style="red",
        )
    )
