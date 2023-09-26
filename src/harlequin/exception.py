class HarlequinExit(Exception):
    pass


class HarlequinError(Exception):
    pass


class HarlequinConnectionError(HarlequinError):
    def __init__(self, msg: str, title: str = "") -> None:
        super().__init__(msg)
        self.title = title


class HarlequinThemeError(HarlequinError):
    pass
