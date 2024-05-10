from __future__ import annotations

from rich.padding import Padding
from rich.syntax import Syntax
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from harlequin.history import History, QueryExecution
from harlequin.messages import WidgetMounted


class HistoryOption(Option):
    PADDING = (0, 1, 0, 1)

    def __init__(self, item: QueryExecution) -> None:
        super().__init__(prompt=Padding(item, pad=self.PADDING))
        self.value = item.query_text


class HistoryList(OptionList):
    BORDER_TITLE = "Query History"
    BORDER_SUBTITLE = "Enter or click to select; Escape to cancel"


class HistoryScreen(Screen[str]):

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
        self.list = HistoryList(*reversed([HistoryOption(q) for q in self.history]))
        self.preview = Static("")
        with Horizontal():
            yield self.list
            with VerticalScroll():
                yield self.preview

    def on_mount(self) -> None:
        scroll = self.query_one(VerticalScroll)
        scroll.border_title = "Highlighted Query Preview"
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
        self.preview.update(
            Syntax(
                code=query,
                lexer="sql",
                theme=self.theme,
                line_numbers=True,
            )
        )
