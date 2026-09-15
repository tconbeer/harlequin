from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, ClassVar

from rich.padding import Padding
from rich.style import Style
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Footer, Input, OptionList
from textual.widgets.option_list import Option
from textual_textarea import TextEditor

from harlequin.history import History, QueryExecution
from harlequin.messages import WidgetMounted

if TYPE_CHECKING:
    from textual.app import RenderResult

FILTER_INTERVAL = 0.2
"""How long the store goes unread while the filter is being typed into."""


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


class QueryPreview(TextEditor, can_focus=False):
    """The highlighted query, for reading.

    Nothing in it can take focus, so a cursor never lands in a query that the
    pane will not let the user change.
    """

    def on_mount(self) -> None:
        assert self.text_input is not None
        self.text_input.can_focus = False


class HistoryScreen(ModalScreen[str]):
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "history-screen--error-label",
    }

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("pageup", "page_up", "Page Up", show=False),
        Binding("pagedown", "page_down", "Page Down", show=False),
    ]
    """The list's keys, so it is still driveable while the filter has focus."""

    class HistoryFiltered(Message):
        """Posted when a filtered read of the query log comes back.

        `history` is None for a read that failed, and `search` is what was
        typed when it started, so a read overtaken by the next one is dropped.
        """

        def __init__(self, history: History | None, search: str) -> None:
            super().__init__()
            self.history = history
            self.search = search

    def __init__(
        self,
        history: History,
        connection: str | None = None,
        theme: str = "harlequin",
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.history = history
        self.connection = connection
        self.theme = theme
        self._filter_timer: Timer | None = None
        self._filter_failed = False
        """Whether the last read failed; one toast per failure streak."""

    def compose(self) -> ComposeResult:
        error_style = self.get_component_rich_style("history-screen--error-label")
        self.error_text_style = Style(
            color=error_style.color, italic=error_style.italic, bold=error_style.bold
        )
        self.filter_input = Input(
            placeholder="Filter by query text", id="history_filter"
        )
        self.list = HistoryList(*self._options())
        self.preview = QueryPreview(
            language="sql", theme=self.theme, read_only=True, use_system_clipboard=False
        )
        with Horizontal(id="history_panes"):
            with Vertical(id="history_filter_pane"):
                yield self.filter_input
                yield self.list
            yield self.preview
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        self.preview.border_title = "Highlighted Query Preview"
        self.filter_input.border_title = "Filter"
        self._show_count()
        self._highlight_first()
        # the filter takes focus, and the screen's bindings drive the list
        self.filter_input.focus()
        self.post_message(WidgetMounted(widget=self))

    def action_cancel(self) -> None:
        """Clear the filter, or leave when there is nothing to clear."""
        if self.filter_input.value:
            self.filter_input.value = ""
        else:
            self.app.pop_screen()

    def action_select(self) -> None:
        self.list.action_select()

    def action_cursor_up(self) -> None:
        self.list.action_cursor_up()

    def action_cursor_down(self) -> None:
        self.list.action_cursor_down()

    def action_page_up(self) -> None:
        # OptionList's two paging actions are the only unannotated ones
        self.list.action_page_up()  # type: ignore[no-untyped-call]

    def action_page_down(self) -> None:
        self.list.action_page_down()  # type: ignore[no-untyped-call]

    @on(Input.Changed, "#history_filter")
    def schedule_filter(self, message: Input.Changed) -> None:
        """Re-read the store for what has been typed, at most once per interval."""
        message.stop()
        if self._filter_timer is None:
            self._filter_timer = self.set_timer(FILTER_INTERVAL, self._read_filtered)

    def _read_filtered(self) -> None:
        self._filter_timer = None
        self.read_history(self.filter_input.value)

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="history_filters",
    )
    def read_history(self, search: str) -> None:
        """The whole store filtered by `search`, not the rows already on screen."""
        try:
            history: History | None = History.recent(
                connection=self.connection, search=search or None
            )
        except sqlite3.Error:
            history = None
        self.post_message(self.HistoryFiltered(history=history, search=search))

    @on(HistoryFiltered)
    def show_filtered_history(self, message: HistoryFiltered) -> None:
        message.stop()
        if message.search != self.filter_input.value:
            # a thread cannot be cancelled mid-read, so a read the next
            # keystroke overtook still arrives
            return
        if message.history is None:
            if not self._filter_failed:
                self._filter_failed = True
                self.app.notify(
                    "Harlequin could not read your query history.",
                    title="Query History",
                    severity="warning",
                )
            return
        self._filter_failed = False
        self.history = message.history
        self.list.clear_options()
        self.list.add_options(self._options())
        self._show_count()
        self._highlight_first()

    @on(OptionList.OptionSelected)
    def insert_query(self, message: OptionList.OptionSelected) -> None:
        message.stop()
        query = getattr(message.option, "value", None)
        assert isinstance(query, str)
        self.dismiss(result=query)

    @on(OptionList.OptionHighlighted)
    def preview_query(self, message: OptionList.OptionHighlighted) -> None:
        message.stop()
        query = getattr(message.option, "value", None)
        assert isinstance(query, str)
        self.preview.text = query

    def _options(self) -> list[HistoryOption]:
        return [
            HistoryOption(query, error_style=self.error_text_style)
            for query in self.history
        ]

    def _highlight_first(self) -> None:
        """Preview the newest of what is listed, so the pane is never blank."""
        if len(self.history):
            self.list.highlighted = 0
        else:
            self.preview.text = ""

    def _show_count(self) -> None:
        count = len(self.history)
        self.list.border_subtitle = (
            "No matching queries"
            if count == 0
            else f"{count:n} quer{'y' if count == 1 else 'ies'}"
        )
