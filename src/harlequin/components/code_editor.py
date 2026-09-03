from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Union

from sqlfmt.api import Mode, format_string
from sqlfmt.exception import SqlfmtError
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.geometry import Offset
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Tab, Tabs, TextArea
from textual.widgets.text_area import EditHistory, Location, Selection
from textual.worker import Worker, WorkerState
from textual_textarea import TextAreaSaved, TextEditor

from harlequin.autocomplete import (
    NO_SYMBOLS,
    BufferSymbols,
    MemberCompleter,
    WordCompleter,
    find_symbols,
)
from harlequin.components.text_modal import ErrorModal
from harlequin.editor_cache import BufferState, load_cache
from harlequin.exception import HarlequinExternalError
from harlequin.external import launch_external_editor
from harlequin.messages import WidgetMounted
from harlequin.statements import find_separators

SYMBOL_SCAN_INTERVAL = 0.3
"""Seconds an edit waits before the buffer is re-read for symbols."""


@dataclass
class EditorState:
    """One buffer's state; the active buffer's lives in the editor, the rest here."""

    text: str = ""
    selection: Selection = field(default_factory=Selection)
    scroll_offset: Offset = field(default_factory=Offset)
    undo_history: Union[EditHistory, None] = None


def _blank_history(template: EditHistory) -> EditHistory:
    """Returns an empty undo history, configured like the template."""
    return EditHistory(
        max_checkpoints=template.max_checkpoints,
        checkpoint_timer=template.checkpoint_timer,
        checkpoint_max_characters=template.checkpoint_max_characters,
    )


class CodeEditor(TextEditor, inherit_bindings=False):
    class Submitted(Message, bubble=True):
        """Posted when user runs the query.

        Attributes:
            lines: The lines of code being submitted.
            cursor: The position of the cursor
        """

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class SymbolsFound(Message):
        """Posted when the loaded buffer's identifiers have been re-read."""

        def __init__(self, symbols: BufferSymbols) -> None:
            super().__init__()
            self.symbols = symbols

    _symbol_scan_timer: Union[Timer, None] = None
    _symbol_scan_failed: bool = False
    """Whether the last symbol scan failed; one toast per failure streak."""

    @on(TextArea.Changed)
    def schedule_symbol_scan(self, message: TextArea.Changed) -> None:
        """Re-read the buffer's symbols, at most once per scan interval."""
        if self._symbol_scan_timer is None:
            self._symbol_scan_timer = self.set_timer(
                SYMBOL_SCAN_INTERVAL, self._scan_for_symbols
            )

    def _scan_for_symbols(self) -> None:
        self._symbol_scan_timer = None
        self.read_symbols(self.text)

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="symbol_scanners",
    )
    def read_symbols(self, text: str) -> None:
        self.post_message(self.SymbolsFound(symbols=find_symbols(text)))

    @on(Worker.StateChanged)
    def handle_symbol_scan_error(self, message: Worker.StateChanged) -> None:
        if (
            message.state == WorkerState.ERROR
            and message.worker.name == "read_symbols"
            and message.worker.error is not None
            and not self._symbol_scan_failed
        ):
            # a scan failure is a degraded buffer, not a reason to die; typing
            # in the same broken buffer would toast on every debounced scan.
            self._symbol_scan_failed = True
            self.app.notify(
                "Harlequin could not read this buffer's identifiers.",
                severity="warning",
            )

    @on(SymbolsFound)
    def reset_symbol_scan_failure(self, message: SymbolsFound) -> None:
        # not stopped: SymbolsFound must keep bubbling to the app.
        self._symbol_scan_failed = False

    def selected_queries(self) -> list[str]:
        """
        Returns the list of queries that intersect
        with the current selection.
        """
        if self.text_input is None or not self.text.strip():
            return []

        if ";" not in self.text:
            return [self.text]

        separators = find_separators(self.text)
        if not separators:
            # a semicolon could be in a string literal,
            # so there may not be query separators even if
            # there are literal semicolons in the text.
            return [self.text]

        # a selection can be made in either direction, so its end
        # can come before its start.
        selection_start = min(self.selection.start, self.selection.end)
        selection_end = max(self.selection.start, self.selection.end)

        # each query spans from the end of the previous separator
        # (or the start of the buffer) through its own separator.
        queries: list[tuple[Location, Location, str]] = []
        query_start: Location = (0, 0)
        for query_end in [*separators, self.text_input.document.end]:
            q = self.text_input.get_text_range(start=query_start, end=query_end).strip()
            if q:
                queries.append((query_start, query_end, q))
            query_start = query_end

        if not queries:
            return []

        # an empty selection (a bare cursor) spans no range, so it only
        # overlaps a query if it sits strictly inside that query.
        overlapping = [
            q
            for start, end, q in queries
            if start < selection_end and end > selection_start
        ]
        if overlapping:
            return overlapping

        # the cursor sits on a boundary between queries (or in the whitespace
        # between them); run the first query that ends at or after the cursor.
        for _, end, q in queries:
            if end >= selection_start:
                return [q]

        # the cursor is in trailing whitespace after the last query.
        return [queries[-1][2]]

    def capture_state(self) -> Union[EditorState, None]:
        """
        Returns the state of the buffer loaded in the editor, or None if the
        editor has not yet composed its TextArea.
        """
        if self.text_input is None:
            return None
        return EditorState(
            text=self.text_input.text,
            selection=self.text_input.selection,
            scroll_offset=self.text_input.scroll_offset,
            undo_history=self.text_input.history,
        )

    def load_state(self, state: EditorState) -> None:
        """Swaps a buffer's state into the editor, in place of what it holds now."""
        if self.text_input is None:
            return
        # load_text clears the history it finds, so hand it a throwaway one and
        # install the buffer's own history afterwards.
        self.text_input.history = _blank_history(self.text_input.history)
        self.text_input.load_text(state.text)
        self.text_input.history = state.undo_history or _blank_history(
            self.text_input.history
        )
        self.text_input.selection = state.selection
        self.text_input.scroll_to(*state.scroll_offset, animate=False)

    def on_mount(self) -> None:
        self.post_message(EditorCollection.EditorSwitched(active_editor=self))
        self.post_message(WidgetMounted(widget=self))
        self.has_shown_clipboard_error = False

    def on_unmount(self) -> None:
        self.post_message(EditorCollection.EditorSwitched(active_editor=None))

    def on_text_area_saved(self, message: TextAreaSaved) -> None:
        self.app.notify(f"Editor contents saved to {message.path}")
        if hasattr(self.app, "data_catalog"):
            self.app.data_catalog.update_file_tree()

    def on_text_area_clipboard_error(self) -> None:
        if not self.has_shown_clipboard_error:
            self.app.notify(
                "Could not access system clipboard.\n"
                "See https://harlequin.sh/docs/troubleshooting/copying-and-pasting",
                severity="error",
                timeout=10,
            )
            self.has_shown_clipboard_error = True

    async def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))

    def action_format(self) -> None:
        if self.text_input is None:
            return
        old_selection = self.text_input.selection
        old_text = self.text

        try:
            formatted_text = format_string(old_text, Mode())
        except SqlfmtError as e:
            self.app.push_screen(
                ErrorModal(
                    title="Formatting Error",
                    header="There was an error while formatting your file:",
                    error=e,
                )
            )
        else:
            if formatted_text != old_text:
                self.text = formatted_text
                self.text_input.selection = old_selection
                self.app.notify("Formatted query.")
            else:
                self.app.notify("Query was already formatted; no changes made.")

    def action_launch_external_editor(self) -> None:
        """Round-trips the buffer through the user's editor.

        Synchronous on the main thread, because the app has to be suspended for
        the editor to own the terminal; the result is assigned to `text`, which
        checkpoints undo history, so the whole round trip is one Ctrl+Z away.
        """
        if self.text_input is None:
            return
        try:
            edit = launch_external_editor(self.app, self.text)
        except HarlequinExternalError as e:
            self.app.push_screen(
                ErrorModal(
                    title="External Editor Error",
                    header=e.title,
                    error=e,
                )
            )
            return
        if edit.text is None:
            self.app.notify(
                f"Your editor exited with status {edit.returncode}; "
                "no changes were made to the buffer.",
                severity="warning",
            )
        elif edit.text != self.text:
            self.text = edit.text

    def action_focus_results_viewer(self) -> None:
        if hasattr(self.app, "action_focus_results_viewer"):
            self.app.action_focus_results_viewer()

    def action_focus_data_catalog(self) -> None:
        if hasattr(self.app, "action_focus_data_catalog"):
            self.app.action_focus_data_catalog()


class EditorCollection(Vertical):
    """
    A row of tabs over a single editor. Switching tabs swaps the loaded buffer's
    state out of the editor and the newly-active buffer's state in.
    """

    BORDER_TITLE = "Query Editor"
    theme: reactive[str] = reactive("harlequin")

    class EditorSwitched(Message):
        def __init__(self, active_editor: Union[CodeEditor, None]) -> None:
            self.active_editor = active_editor
            super().__init__()

    def __init__(
        self,
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa: A002
        classes: Union[str, None] = None,
        disabled: bool = False,
        language: str = "sql",
        theme: str = "harlequin",
    ):
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self.language = language
        self.counter = 0
        self._word_completer: WordCompleter | None = None
        self._member_completer: MemberCompleter | None = None
        self._buffer_symbols: BufferSymbols = NO_SYMBOLS
        self.startup_cache = load_cache()
        self.buffer_states: dict[str, EditorState] = {}
        self.loaded_buffer_id: str | None = None
        self.tabs = Tabs()
        self.tabs.can_focus = False
        self.editor = CodeEditor(id="buffer", language=language, theme=theme)
        self.theme = theme

    def compose(self) -> ComposeResult:
        yield self.tabs
        yield self.editor

    @property
    def current_editor(self) -> CodeEditor:
        return self.editor

    @property
    def active(self) -> Union[str, None]:
        """The ID of the active buffer's tab."""
        return self.tabs.active or None

    @property
    def tab_count(self) -> int:
        return len(self.buffer_states)

    @property
    def buffers(self) -> List[BufferState]:
        """The state of every buffer, in tab order, for the editor cache."""
        self._save_loaded_buffer()
        return [
            BufferState(selection=state.selection, text=state.text)
            for state in self.buffer_states.values()
        ]

    @property
    def active_buffer_index(self) -> int:
        buffer_ids = list(self.buffer_states)
        if self.loaded_buffer_id is None or self.loaded_buffer_id not in buffer_ids:
            return 0
        return buffer_ids.index(self.loaded_buffer_id)

    @property
    def member_completer(self) -> MemberCompleter | None:
        return self._member_completer

    @member_completer.setter
    def member_completer(self, new_completer: MemberCompleter) -> None:
        self._member_completer = new_completer
        new_completer.update_buffer_symbols(self._buffer_symbols)
        self.editor.member_completer = new_completer

    @property
    def word_completer(self) -> WordCompleter | None:
        return self._word_completer

    @word_completer.setter
    def word_completer(self, new_completer: WordCompleter) -> None:
        self._word_completer = new_completer
        new_completer.update_buffer_symbols(self._buffer_symbols)
        self.editor.word_completer = new_completer

    @on(CodeEditor.SymbolsFound)
    def update_completer_symbols(self, message: CodeEditor.SymbolsFound) -> None:
        """Hand the loaded buffer's symbols to the completers.

        They are kept here as well, since the app swaps in whole new completers
        every time it rebuilds them from the catalog. The message goes on to the
        app, which asks the Data Catalog to load the items the buffer names.
        """
        self._buffer_symbols = message.symbols
        for completer in (self._word_completer, self._member_completer):
            if completer is not None:
                completer.update_buffer_symbols(message.symbols)

    async def on_mount(self) -> None:
        if self.startup_cache is not None and self.startup_cache.buffers:
            for buffer in self.startup_cache.buffers:
                await self.action_new_buffer(state=buffer, activate=False)
            self._activate_cached_buffer(self.startup_cache.focus_index)
        else:
            await self.action_new_buffer()
        self.editor.theme = self.theme
        self.editor.word_completer = self.word_completer
        self.editor.member_completer = self.member_completer
        self.remove_class("premount")
        self.post_message(WidgetMounted(widget=self))

    def on_focus(self) -> None:
        self.editor.focus()

    def on_tabs_tab_activated(self, message: Tabs.TabActivated) -> None:
        message.stop()
        new_buffer_id = message.tab.id
        if new_buffer_id is None or new_buffer_id == self.loaded_buffer_id:
            return
        self._save_loaded_buffer()
        self.loaded_buffer_id = new_buffer_id
        state = self.buffer_states.get(new_buffer_id)
        if state is not None:
            self.editor.load_state(state)
        self.post_message(self.EditorSwitched(active_editor=self.editor))
        self.editor.focus()

    def watch_theme(self, theme: str) -> None:
        if self.editor.is_mounted:
            self.editor.theme = theme

    async def insert_buffer_with_text(self, query_text: str) -> None:
        state = BufferState(selection=Selection(), text=query_text)
        await self.action_new_buffer(state=state)

    async def action_new_buffer(
        self, state: Union[BufferState, None] = None, activate: bool = True
    ) -> CodeEditor:
        self.counter += 1
        new_buffer_id = f"tab-{self.counter}"
        self.buffer_states[new_buffer_id] = (
            EditorState(text=state.text, selection=state.selection)
            if state is not None
            else EditorState()
        )
        await self.tabs.add_tab(Tab(f"Tab {self.counter}", id=new_buffer_id))
        if activate:
            # adding the first tab activates it; any later tab has to be
            # activated here to swap its state into the editor.
            self.tabs.active = new_buffer_id
            self.editor.focus()
        if self.counter > 1:
            self.remove_class("hide-tabs")
        return self.editor

    def action_close_buffer(self) -> None:
        if self.tab_count > 1:
            if self.tab_count == 2:
                self.add_class("hide-tabs")
            closed_buffer_id = self.active
            # the editor's contents belong to the buffer being closed, so they
            # are dropped rather than saved when the next tab is activated.
            self.loaded_buffer_id = None
            if closed_buffer_id is not None:
                self.buffer_states.pop(closed_buffer_id, None)
                self.tabs.remove_tab(closed_buffer_id)
        else:
            self.editor.load_state(EditorState())
        self.editor.focus()

    def action_next_buffer(self) -> None:
        if self.tab_count < 2:
            return
        self.tabs.action_next_tab()

    def _activate_cached_buffer(self, focus_index: int) -> None:
        """Reopens the buffer that was active when the cache was written."""
        buffer_ids = list(self.buffer_states)
        if not 0 <= focus_index < len(buffer_ids):
            focus_index = 0
        self.tabs.active = buffer_ids[focus_index]

    def _save_loaded_buffer(self) -> None:
        """Copies the editor's contents back into the buffer they were loaded from."""
        if self.loaded_buffer_id is None:
            return
        state = self.editor.capture_state()
        if state is not None and self.loaded_buffer_id in self.buffer_states:
            self.buffer_states[self.loaded_buffer_id] = state
