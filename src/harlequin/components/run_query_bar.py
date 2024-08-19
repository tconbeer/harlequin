from __future__ import annotations

from typing import Union

from textual import on
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
        show_cancel_button: bool = False,
    ) -> None:
        self.max_results = max_results
        self.show_cancel_button = show_cancel_button
        super().__init__(
            *children, name=name, id=id, classes=classes, disabled=disabled
        )

    def compose(self) -> ComposeResult:
        self.transaction_button = Button(
            "Tx: Auto", id="transaction_button", classes="hidden"
        )
        self.transaction_button.tooltip = "Click to change modes"
        self.commit_button = Button("🡅", id="commit_button", classes="hidden")
        self.commit_button.tooltip = "Commit transaction"
        self.rollback_button = Button("⮌", id="rollback_button", classes="hidden")
        self.rollback_button.tooltip = "Roll back transaction"
        self.limit_checkbox = Checkbox("Limit ", id="limit_checkbox")
        self.limit_input = Input(
            str(min(500, self.max_results)),
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
        self.run_button = Button("Run Query", id="run_query")
        self.cancel_button = Button("Cancel Query", id="cancel_query")
        self.cancel_button.add_class("hidden")
        with Horizontal(id="transaction_buttons"):
            yield self.transaction_button
            yield self.commit_button
            yield self.rollback_button
        with Horizontal(id="run_buttons"):
            yield self.limit_checkbox
            yield self.limit_input
            yield self.run_button
            yield self.cancel_button

    def on_mount(self) -> None:
        if self.app.is_headless:
            self.limit_input.cursor_blink = False

    @on(Input.Changed, "#limit_input")
    def handle_new_limit_value(self, message: Input.Changed) -> None:
        """
        check and uncheck the box for valid/invalid input
        """
        if (
            message.input.value
            and message.validation_result
            and message.validation_result.is_valid
        ):
            self.limit_checkbox.value = True
        else:
            self.limit_checkbox.value = False

    @property
    def limit_value(self) -> int | None:
        if not self.limit_checkbox.value or not self.limit_input.is_valid:
            return None
        try:
            return int(self.limit_input.value)
        except ValueError:
            return None

    def set_not_responsive(self) -> None:
        self.add_class("non-responsive")
        if self.show_cancel_button:
            with self.app.batch_update():
                self.run_button.add_class("hidden")
                self.cancel_button.remove_class("hidden")

    def set_responsive(self) -> None:
        self.remove_class("non-responsive")
        if self.show_cancel_button:
            with self.app.batch_update():
                self.run_button.remove_class("hidden")
                self.cancel_button.add_class("hidden")
