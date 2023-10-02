from typing import Union

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.validation import Integer
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input


class RunQueryBar(Horizontal):
    def __init__(
        self,
        *children: Widget,
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa
        classes: Union[str, None] = None,
        disabled: bool = False,
        max_results: int = 10_000,
    ) -> None:
        self.max_results = max_results
        super().__init__(
            *children, name=name, id=id, classes=classes, disabled=disabled
        )

    def compose(self) -> ComposeResult:
        yield Checkbox("Limit ", id="limit_checkbox")
        yield Input(
            "500",
            id="limit_input",
            validators=Integer(
                minimum=0,
                maximum=self.max_results if self.max_results > 0 else None,
                failure_description=(
                    f"Please enter a number between 0 and {self.max_results}."
                    if self.max_results > 0
                    else "Please enter a number greater than 0."
                ),
            ),
        )
        yield Button("Run Query", id="run_query")

    def set_not_responsive(self) -> None:
        self.add_class("non-responsive")

    def set_responsive(self) -> None:
        self.remove_class("non-responsive")

    def on_mount(self) -> None:
        self.checkbox = self.query_one(Checkbox)
        self.input = self.query_one(Input)
        self.button = self.query_one(Button)

    def on_input_changed(self, message: Input.Changed) -> None:
        """
        check and uncheck the box for valid/invalid input
        """
        if message.input.id == "limit_input":
            if (
                message.input.value
                and message.validation_result
                and message.validation_result.is_valid
            ):
                self.checkbox.value = True
            else:
                self.checkbox.value = False
