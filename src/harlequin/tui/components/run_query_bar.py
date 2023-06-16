from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.validation import Integer
from textual.widgets import Button, Checkbox, Input


class RunQueryBar(Horizontal):
    def compose(self) -> ComposeResult:
        yield Checkbox("Limit ", id="limit_checkbox")
        yield Input(
            "500",
            id="limit_input",
            validators=Integer(
                minimum=0,
                maximum=50000,
                failure_description="Please enter a number between 0 and 50,000.",
            ),
        )
        yield Button("Run Query", id="run_query")

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

    def set_not_responsive(self) -> None:
        self.checkbox.add_class("non-responsive")
        self.input.add_class("non-responsive")
        self.button.add_class("non-responsive")

    def set_responsive(self) -> None:
        self.checkbox.remove_class("non-responsive")
        self.input.remove_class("non-responsive")
        self.button.remove_class("non-responsive")
