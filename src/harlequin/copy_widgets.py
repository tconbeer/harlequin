from __future__ import annotations

from typing import Callable

from textual.validation import ValidationResult, Validator
from textual.widgets import Input as Input
from textual.widgets import Label
from textual.widgets import Select as Select
from textual.widgets import Switch as Switch
from textual_textarea import PathInput as PathInput


class NoFocusLabel(Label, can_focus=False):
    pass


class CustomValidator(Validator):
    """Adapts an option's `validator` callable to Textual's Validator interface.

    Lives here rather than in `options.py` because subclassing `Validator` binds
    Textual at class-definition time, which would make declaring an option cost
    the framework.
    """

    def __init__(
        self,
        validator: Callable[[str], tuple[bool, str | None]] | None = None,
        failure_description: str | None = None,
    ) -> None:
        super().__init__(failure_description)
        self.validator = validator or (lambda _: (True, ""))

    def validate(self, value: str) -> ValidationResult:
        try:
            is_valid, message = self.validator(value)
        except Exception as e:
            return self.failure(str(e))

        if is_valid:
            return self.success()
        else:
            return self.failure(message or "Validation failed.")
