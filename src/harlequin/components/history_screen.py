from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from rich.padding import Padding
from rich.style import Style
from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Footer, Input, OptionList, Select
from textual.widgets.option_list import Option
from textual_textarea import TextEditor

from harlequin.history import History, QueryExecution
from harlequin.messages import WidgetMounted
from harlequin.query_log import PROGRAMS, STATUSES

if TYPE_CHECKING:
    from textual.app import RenderResult

FILTER_INTERVAL = 0.2
"""How long the store goes unread while the filter is being typed into."""


@dataclass(frozen=True)
class Filters:
    """What the filter controls are asking of the store.

    The value a read was made for, so that a read the controls have moved on
    from can be dropped, and one that asks again for what is already listed can
    leave the list alone.
    """

    search: str = ""
    program: str | None = None
    status: str | None = None


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


class QueryPreview(TextEditor, inherit_bindings=False):
    """The highlighted query, for reading.

    Its keys are the screen's: `inherit_bindings=False` so that the editor's
    own do not come and go from the footer as focus moves.
    """


class HistoryScreen(ModalScreen[str]):
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "history-screen--error-label",
    }

    class HistoryFiltered(Message):
        """Posted when a filtered read of the query log comes back.

        `history` is None for a read that failed, and `filters` is what the
        controls asked for when it started.
        """

        def __init__(self, history: History | None, filters: Filters) -> None:
            super().__init__()
            self.history = history
            self.filters = filters

    def __init__(
        self,
        history: History,
        connection_hash: str | None = None,
        theme: str = "harlequin",
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.history = history
        self.applied_filters = Filters()
        """What `history` was read for."""
        self.connection_hash = connection_hash
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
            placeholder="Filter by query text",
            id="history_filter",
            select_on_focus=False,
        )
        self.program_select: Select[str] = Select(
            options=[(program, program) for program in PROGRAMS],
            prompt="Any program",
            id="history_program",
        )
        self.status_select: Select[str] = Select(
            options=[(status, status) for status in STATUSES],
            prompt="Any outcome",
            id="history_status",
        )
        self.filter_controls = Horizontal(id="history_filter_controls")
        # before mount, so the screen never paints the controls it opens without
        self.filter_controls.display = False
        self.list = HistoryList(*self._options())
        self.preview = QueryPreview(
            language="sql",
            theme=self.theme,
            read_only=True,
            # takes focus so a long query can be scrolled, but draws no cursor
            # and no cursor line, and scrolls as any other container does
            show_cursor=False,
            use_system_clipboard=False,
        )
        with self.filter_controls:
            yield self.filter_input
            yield self.program_select
            yield self.status_select
        with Horizontal(id="history_panes"):
            yield self.list
            yield self.preview
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        self.preview.border_title = "Highlighted Query Preview"
        self.filter_input.border_title = "Filter"
        self.program_select.border_title = "Program"
        self.status_select.border_title = "Outcome"
        self._show_count()
        self._highlight_first()
        # the screen opens on the list, which is what it is for; the controls
        # are reached by typing, or by the key that shows them
        self.list.focus()
        self.post_message(WidgetMounted(widget=self))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Leave enter to an open dropdown, whose key it is.

        Only enter: the screen's binding for it is a priority one, because the
        filter input binds enter to its own submit, and a priority binding that
        declined by doing nothing would swallow the key. Declining here lets it
        reach the widget that has focus.
        """
        if action == "select" and self._dropdown_is_open:
            return False
        return True

    def action_toggle_filters(self) -> None:
        """Show the filter controls and put the cursor in them, or put them away.

        Hiding forgets what they were set to: a filter still narrowing the list
        from behind a hidden control is a list nobody can explain.
        """
        if self.filter_controls.display:
            self.filter_controls.display = False
            self.list.focus()
            self._clear_filters()
        else:
            self.filter_controls.display = True
            self.filter_input.focus()

    def action_cancel(self) -> None:
        """Empty the filter, put the controls away, or leave the screen."""
        if not self._filters_have_focus:
            self.app.pop_screen()
        elif self.filter_input.value:
            self.filter_input.value = ""
        else:
            self.action_toggle_filters()

    def action_select(self) -> None:
        """Open a dropdown, take the term to the list, or take the query."""
        for select in (self.program_select, self.status_select):
            if select.has_focus:
                select.action_show_overlay()
                return
        if self.filter_input.has_focus:
            self.list.focus()
        else:
            self.list.action_select()

    def on_key(self, event: events.Key) -> None:
        """Typing over the list starts a search rather than being swallowed."""
        if not self.list.has_focus:
            return
        typed = event.character
        if typed is None or not typed.isprintable():
            return
        event.stop()
        event.prevent_default()
        self.filter_controls.display = True
        self.filter_input.focus()
        self.filter_input.insert_text_at_cursor(typed)

    @on(Input.Changed, "#history_filter")
    def schedule_filter(self, message: Input.Changed) -> None:
        """Re-read the store for what has been typed, at most once per interval."""
        message.stop()
        if self._filter_timer is None:
            self._filter_timer = self.set_timer(FILTER_INTERVAL, self._read_filtered)

    @on(Select.Changed)
    def filter_by_selection(self, message: Select.Changed) -> None:
        """Re-read the store now: a dropdown is one choice, not a stream of them."""
        message.stop()
        self.read_history(self._selected_filters())

    def _read_filtered(self) -> None:
        self._filter_timer = None
        self.read_history(self._selected_filters())

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="history_filters",
    )
    def read_history(self, filters: Filters) -> None:
        """The whole store, filtered by what the controls are set to."""
        try:
            history: History | None = History.recent(
                connection=self.connection_hash,
                search=filters.search or None,
                program=filters.program,
                status=filters.status,
            )
        except (sqlite3.Error, OSError):
            history = None
        self.post_message(self.HistoryFiltered(history=history, filters=filters))

    @on(HistoryFiltered)
    def show_filtered_history(self, message: HistoryFiltered) -> None:
        message.stop()
        if message.filters != self._selected_filters():
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
        if message.filters == self.applied_filters and self.list.option_count:
            # an edit that came to nothing inside the debounce window still
            # reads, and rebuilding for it would lose the highlight
            return
        self.applied_filters = message.filters
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

    @property
    def _dropdown_is_open(self) -> bool:
        return self.program_select.expanded or self.status_select.expanded

    @property
    def _filters_have_focus(self) -> bool:
        return any(
            control.has_focus
            for control in (self.filter_input, self.program_select, self.status_select)
        )

    def _selected_filters(self) -> Filters:
        """What the three controls are asking for, now."""
        return Filters(
            search=self.filter_input.value,
            program=_chosen(self.program_select),
            status=_chosen(self.status_select),
        )

    def _clear_filters(self) -> None:
        """Set all three back to everything, which re-reads the store."""
        self.filter_input.value = ""
        self.program_select.clear()
        self.status_select.clear()

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


def _chosen(select: "Select[str]") -> str | None:
    """One dropdown's value, where it has one rather than its prompt."""
    return None if select.is_blank() else str(select.value)
