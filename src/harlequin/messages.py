from typing import TYPE_CHECKING

from textual.message import Message

if TYPE_CHECKING:
    from textual.widget import Widget


class WidgetMounted(Message):
    def __init__(self, widget: "Widget") -> None:
        super().__init__()
        self.widget = widget
