from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich.padding import Padding
from rich.style import Style
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import OptionList
from textual.widgets.option_list import Option
from textual_textarea import TextEditor

from harlequin.history import History, QueryExecution
from harlequin.messages import WidgetMounted

if TYPE_CHECKING:
    from textual.app import RenderResult


class HistoryOption(Option):
    PADDING = (0, 1, 0, 1)

    def __init__(self, item: QueryExecution, error_style: Style) -> None:
        super().__init__(prompt="")
        self.item = item
        self.error_style = error_style
        self.value = item.query_text

    @property
    def prompt(self) -> RenderResult:
        """The prompt for the option."""
        return Padding(self.item.render(error_style=self.error_style), pad=self.PADDING)

    def __rich__(self) -> RenderResult:
        return self.prompt

    def visualize(self) -> object:
        return self.prompt


class HistoryList(OptionList):
    BORDER_TITLE = "Query History"
    BORDER_SUBTITLE = "Enter or click to select; Escape to cancel"


class HistoryScreen(Screen[str]):
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "history-screen--error-label",
    }

    def __init__(
        self,
        history: History,
        theme: str = "harlequin",
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.history = history
        self.theme = theme

    def compose(self) -> ComposeResult:
        error_style = self.get_component_rich_style("history-screen--error-label")
        error_text_style = Style(
            color=error_style.color, italic=error_style.italic, bold=error_style.bold
        )
        self.list = HistoryList(
            *[HistoryOption(q, error_style=error_text_style) for q in self.history]
        )
        self.preview = TextEditor(
            language="sql", theme=self.theme, read_only=True, use_system_clipboard=False
        )
        with Horizontal():
            yield self.list
            yield self.preview

    def on_mount(self) -> None:
        self.preview.border_title = "Highlighted Query Preview"
        self.post_message(WidgetMounted(widget=self))

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_select(self) -> None:
        self.list.action_select()

    @on(OptionList.OptionSelected)
    def insert_query(self, message: OptionList.OptionSelected) -> None:
        message.stop()
        query = getattr(message.option, "value", None)
        assert isinstance(query, str)
        self.dismiss(result=query)

    @on(OptionList.OptionHighlighted)
    def preview_query(self, message: OptionList.OptionSelected) -> None:
        message.stop()
        query = getattr(message.option, "value", None)
        assert isinstance(query, str)
        self.preview.text = query
