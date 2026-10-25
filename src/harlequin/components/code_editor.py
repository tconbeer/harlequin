from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Union

from sqlfmt.api import Mode, format_string
from sqlfmt.exception import SqlfmtError
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.geometry import Offset
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Input, Label, Tab, Tabs, TextArea
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
from harlequin.components.confirm_modal import ConfirmModal
from harlequin.components.save_query_screen import SaveQueryScreen, TypeAPath
from harlequin.components.text_modal import ErrorModal
from harlequin.editor_cache import BufferState, adopt_recovery, load_cache
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
    path: Union[Path, None] = None
    """File this buffer was opened from. None for an anonymous query tab."""
    saved_text: str = ""
    """Editor text at the last successful load or save of `path`."""


def _literal_tab_label(text: str) -> Content:
    """A tab label that shows `text` as typed, including brackets."""
    return Content.from_text(text, markup=False)


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
        if self.text_input is not None:
            self.post_message(WidgetMounted(widget=self.text_input))
        self.has_shown_clipboard_error = False

    def on_unmount(self) -> None:
        self.post_message(EditorCollection.EditorSwitched(active_editor=None))

    def on_text_area_saved(self, message: TextAreaSaved) -> None:
        # The path is literal text. "[/b]" in it is not markup.
        self.app.notify(
            f"Editor contents saved to {message.path}",
            markup=False,
        )
        if hasattr(self.app, "data_catalog"):
            self.app.data_catalog.update_file_tree()

    async def action_save(self) -> None:
        """Save the open file, or ask for a folder and a name when it has none."""
        parent = self.parent
        if not isinstance(parent, EditorCollection):
            await super().action_save()
            return
        if parent.active_file_path() is not None:
            parent.save_active_file()
            return
        await parent.save_unbound_buffer()

    async def prompt_for_typed_path(self) -> None:
        """Ask for a whole path on one line. The tree save is the primary way."""
        await super().action_save()

    @on(Input.Submitted, "#textarea__open_input")
    async def open_submitted_file(self, message: Input.Submitted) -> None:
        """Load the chosen file into this buffer and bind that path to it.

        The text editor's own open handler replaces the text and leaves the
        buffer's path alone, so the next save would write these bytes onto
        the previous file.
        """
        message.prevent_default()
        message.stop()
        chosen = Path(message.input.value).expanduser()
        await self._clear_footer_input()
        parent = self.parent
        if isinstance(parent, EditorCollection):
            parent.load_into_active_buffer(chosen)

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
        old_selection = self.text_input.selection
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
            # assigning text moves the cursor to the start of the document, and
            # a shorter buffer may no longer hold the position it was at.
            self.text_input.selection = Selection(
                self.text_input.clamp_visitable(old_selection.start),
                self.text_input.clamp_visitable(old_selection.end),
            )

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
        self.recovered_cache, self.recovered_from = adopt_recovery()
        self.buffer_states: dict[str, EditorState] = {}
        self.loaded_buffer_id: str | None = None
        self.tabs = Tabs()
        self.tabs.can_focus = False
        self.editor = CodeEditor(id="buffer", language=language, theme=theme)
        self.theme = theme

    def compose(self) -> ComposeResult:
        yield self.tabs
        yield Label(
            Content.from_text("Not a file", markup=False),
            id="buffer-path",
        )
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
            BufferState(
                selection=state.selection,
                text=state.text,
                path=None if state.path is None else str(state.path),
            )
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
        # a recovered session started from the cache, so it is strictly newer
        cache = self.recovered_cache or self.startup_cache
        if cache is not None and cache.buffers:
            for buffer in cache.buffers:
                await self.action_new_buffer(state=buffer, activate=False)
            self._activate_cached_buffer(cache.focus_index)
        else:
            await self.action_new_buffer()
        if self.recovered_cache is not None:
            self.notify(
                "Recovered buffers from a session that ended unexpectedly.",
                title="Buffers recovered",
            )
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
        self._refresh_path_line()
        self.post_message(self.EditorSwitched(active_editor=self.editor))
        self.editor.focus()

    @on(TextArea.Changed)
    def mark_unsaved_edits(self, _message: TextArea.Changed) -> None:
        """Refresh the open file's tab while it differs from the saved text.

        Not stopped: the editor still re-reads symbols from the same event.
        Anonymous tabs stay unlabeled, so typing in one does not mark it dirty.
        """
        buffer_id = self.loaded_buffer_id
        if buffer_id is None:
            return
        state = self.buffer_states.get(buffer_id)
        if state is None or state.path is None:
            return
        self._refresh_tab_label(buffer_id)

    def watch_theme(self, theme: str) -> None:
        if self.editor.is_mounted:
            self.editor.theme = theme

    async def insert_buffer_with_text(self, query_text: str) -> None:
        state = BufferState(selection=Selection(), text=query_text)
        await self.action_new_buffer(state=state)

    async def action_new_buffer(
        self, state: Union[BufferState, None] = None, activate: bool = True
    ) -> CodeEditor:
        if self._closing or self._pruning:
            # a collection on its way out mounts nothing, and `Tabs` raises
            # when it activates a tab that never reached the DOM.
            return self.editor
        self.counter += 1
        new_buffer_id = f"tab-{self.counter}"
        editor_state = (
            EditorState(text=state.text, selection=state.selection)
            if state is not None
            else EditorState()
        )
        if state is not None and state.path:
            editor_state.path = Path(state.path)
            editor_state.saved_text = state.text
        self.buffer_states[new_buffer_id] = editor_state
        if editor_state.path is not None:
            tab_label: Union[Content, str] = _literal_tab_label(editor_state.path.name)
        else:
            tab_label = f"Tab {self.counter}"
        await self.tabs.add_tab(Tab(tab_label, id=new_buffer_id))
        if activate:
            # adding the first tab activates it; any later tab has to be
            # activated here to swap its state into the editor.
            self.tabs.active = new_buffer_id
            self.editor.focus()
        self._update_tab_visibility()
        return self.editor

    async def open_file(self, path: Path) -> None:
        """Load `path` into one query tab, or focus the tab that already has it.

        A directory is ignored: the tree expands and collapses those itself.
        A binary or unreadable file notifies and leaves the editor alone.
        """
        resolved = self._resolve_file(path)
        if resolved is None or self._focus_open_path(resolved):
            return
        text = self._read_text_file(resolved)
        if text is None:
            return
        self._save_loaded_buffer()
        if self._can_reuse_blank():
            assert self.loaded_buffer_id is not None
            self._load_file_into(self.loaded_buffer_id, resolved, text)
            return
        # FileSelected is posted again when the file node expands. The path has
        # to be registered before the first await, or that second message opens
        # a duplicate tab.
        self.counter += 1
        new_buffer_id = f"tab-{self.counter}"
        self.buffer_states[new_buffer_id] = EditorState(
            text=text, path=resolved, saved_text=text
        )
        await self.tabs.add_tab(
            Tab(_literal_tab_label(resolved.name), id=new_buffer_id)
        )
        self._update_tab_visibility()
        self.tabs.active = new_buffer_id
        self.editor.focus()

    def load_into_active_buffer(self, path: Path) -> None:
        """Replace the active buffer with `path`, or focus a buffer that has it.

        The active buffer's path becomes `path`. A later save writes `path`,
        not the file this buffer used to be. A dirty file buffer asks first.
        """
        resolved = self._resolve_file(path)
        if resolved is None or self._focus_open_path(resolved):
            return
        text = self._read_text_file(resolved)
        if text is None:
            return
        buffer_id = self.loaded_buffer_id
        if buffer_id is None:
            return

        def replace_if_confirmed(confirmed: Union[bool, None]) -> None:
            if confirmed and self.loaded_buffer_id == buffer_id:
                self._load_file_into(buffer_id, resolved, text)

        if self._loaded_file_is_dirty():
            self.app.push_screen(
                ConfirmModal(
                    prompt="Discard unsaved changes to this file?",
                    confirm_label="Discard",
                    cancel_label="Cancel",
                ),
                callback=replace_if_confirmed,
            )
            return
        self._load_file_into(buffer_id, resolved, text)

    def active_file_path(self) -> Union[Path, None]:
        """The path bound to the loaded buffer, if it was opened from a file."""
        buffer_id = self.loaded_buffer_id
        if buffer_id is None:
            return None
        state = self.buffer_states.get(buffer_id)
        if state is None:
            return None
        return state.path

    def location_text(self) -> str:
        """The open buffer's full path, or a note that it is not a file."""
        path = self.active_file_path()
        if path is None:
            return "Not a file"
        return str(path)

    def _refresh_path_line(self) -> None:
        if not self.is_mounted:
            return
        label = self.query_one("#buffer-path", Label)
        label.update(Content.from_text(self.location_text(), markup=False))

    async def save_unbound_buffer(self) -> None:
        """Ask for a mounted folder and a file name, then bind this buffer."""
        catalog = getattr(self.app, "data_catalog", None)
        mounts = list(catalog.file_mounts) if catalog is not None else []
        if not mounts:
            await self.editor.prompt_for_typed_path()
            return

        def chose(result: Union[Path, TypeAPath, None]) -> None:
            if isinstance(result, TypeAPath):
                self.run_worker(self.editor.prompt_for_typed_path())
                return
            if result is None:
                return
            self.save_unbound_as(result)

        self.app.push_screen(SaveQueryScreen(mounts), chose)

    def save_unbound_as(self, path: Path) -> None:
        """Bind the loaded buffer to `path` and write it, after the checks."""
        buffer_id = self.loaded_buffer_id
        if buffer_id is None:
            return
        try:
            resolved = path.expanduser().resolve()
        except OSError as exc:
            self._notify_path(f"Could not save {path.name}: {exc}")
            return
        if any(
            other_id != buffer_id and state.path == resolved
            for other_id, state in self.buffer_states.items()
        ):
            self._focus_open_path(resolved)
            self.notify(
                "That file is already open.",
                severity="warning",
                markup=False,
            )
            return
        if resolved.exists():

            def overwrite(confirmed: Union[bool, None]) -> None:
                if confirmed and self.loaded_buffer_id == buffer_id:
                    self._bind_and_write(buffer_id, resolved)

            self.app.push_screen(
                ConfirmModal(
                    prompt=f"Overwrite {resolved.name}?",
                    confirm_label="Overwrite",
                    cancel_label="Cancel",
                ),
                callback=overwrite,
            )
            return
        self._bind_and_write(buffer_id, resolved)

    def _bind_and_write(self, buffer_id: str, path: Path) -> None:
        """Write this buffer's exact UTF-8 bytes to `path` and bind it."""
        state = self.buffer_states.get(buffer_id)
        if state is None:
            return
        text = self.editor.text if self.loaded_buffer_id == buffer_id else state.text
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
        except (OSError, UnicodeError) as exc:
            self.app.push_screen(
                ErrorModal(
                    title="Save File Error",
                    header="There was an error when attempting to save your file:",
                    error=exc,
                )
            )
            return
        state.path = path
        state.text = text
        state.saved_text = text
        self._refresh_tab_label(buffer_id)
        self._update_tab_visibility()
        catalog = getattr(self.app, "data_catalog", None)
        if catalog is not None:
            catalog.update_file_tree()
        self.notify(f"Editor contents saved to {path}", markup=False)

    def save_active_file(self) -> None:
        """Write the loaded buffer to its path and clear the unsaved mark."""
        self._save_loaded_buffer()
        buffer_id = self.loaded_buffer_id
        if buffer_id is None:
            return
        state = self.buffer_states.get(buffer_id)
        if state is None or state.path is None:
            return
        try:
            # Path.write_text translates \n to \r\n on Windows, so a buffer that
            # already holds \r\n would be saved as \r\r\n.
            state.path.write_bytes(state.text.encode("utf-8"))
        except (OSError, UnicodeError) as exc:
            self.app.push_screen(
                ErrorModal(
                    title="Save File Error",
                    header="There was an error when attempting to save your file:",
                    error=exc,
                )
            )
            return
        state.saved_text = state.text
        self._refresh_tab_label(buffer_id)
        self.editor.post_message(TextAreaSaved(path=state.path))

    def action_close_buffer(self) -> None:
        if self._loaded_file_is_dirty():
            self.app.push_screen(
                ConfirmModal(
                    prompt="Discard unsaved changes to this file?",
                    confirm_label="Discard",
                    cancel_label="Cancel",
                ),
                callback=self._discard_if_confirmed,
            )
            return
        self._close_loaded_buffer()

    def _discard_if_confirmed(self, confirmed: Union[bool, None]) -> None:
        if confirmed:
            self._close_loaded_buffer()

    def _close_loaded_buffer(self) -> None:
        if self.tab_count > 1:
            closed_buffer_id = self.active
            # the editor's contents belong to the buffer being closed, so they
            # are dropped rather than saved when the next tab is activated.
            self.loaded_buffer_id = None
            if closed_buffer_id is not None:
                self.buffer_states.pop(closed_buffer_id, None)
                self.tabs.remove_tab(closed_buffer_id)
        else:
            blank = EditorState()
            buffer_id = self.loaded_buffer_id
            if buffer_id is not None:
                previous = self.buffer_states.get(buffer_id)
                if previous is not None and previous.path is not None:
                    self.buffer_states[buffer_id] = blank
                    self._refresh_tab_label(buffer_id)
            self.editor.load_state(blank)
        self._update_tab_visibility()
        self._refresh_path_line()
        self.editor.focus()

    async def action_close_all_buffers(self) -> None:
        """Close every buffer, leaving one empty one.

        Confirms when any buffer holds text, and when a file buffer differs
        from its last loaded or saved text. A file wiped to blank or
        whitespace is still an unsaved edit.
        """
        if not self._close_all_would_discard_work():
            await self._close_all_buffers()
            return

        async def close_if_confirmed(confirmed: bool | None) -> None:
            if confirmed:
                await self._close_all_buffers()

        if self.tab_count > 1:
            prompt = (
                f"Close all {self.tab_count} buffers? Their text will be discarded."
            )
            confirm_label = "Close All"
        else:
            prompt = "Close this buffer? Its text will be discarded."
            confirm_label = "Close"
        self.app.push_screen(
            ConfirmModal(
                prompt=prompt, confirm_label=confirm_label, cancel_label="Cancel"
            ),
            callback=close_if_confirmed,
        )

    async def _close_all_buffers(self) -> None:
        kept_buffer_id = self.active
        for buffer_id in list(self.buffer_states):
            if buffer_id != kept_buffer_id:
                # a second close-all can be part way through the same loop
                if self.buffer_states.pop(buffer_id, None) is not None:
                    await self.tabs.remove_tab(buffer_id)
        if kept_buffer_id is not None:
            # the emptied buffer is anonymous again, so a later save of its
            # text does not write the file this tab used to be.
            self.buffer_states[kept_buffer_id] = EditorState()
            self._refresh_tab_label(kept_buffer_id)
        self._update_tab_visibility()
        self._refresh_path_line()
        self.editor.load_state(EditorState())
        self.editor.focus()

    def action_next_buffer(self) -> None:
        if self.tab_count < 2:
            return
        self.tabs.action_next_tab()

    def _activate_cached_buffer(self, focus_index: int) -> None:
        """Reopens the buffer that was active when the cache was written."""
        buffer_ids = list(self.buffer_states)
        if not buffer_ids:
            # a collection on its way out opened none of them
            return
        if not 0 <= focus_index < len(buffer_ids):
            focus_index = 0
        self.tabs.active = buffer_ids[focus_index]

    def _save_loaded_buffer(self) -> None:
        """Copies the editor's contents back into the buffer they were loaded from."""
        if self.loaded_buffer_id is None:
            return
        state = self.editor.capture_state()
        if state is not None and self.loaded_buffer_id in self.buffer_states:
            previous = self.buffer_states[self.loaded_buffer_id]
            state.path = previous.path
            state.saved_text = previous.saved_text
            self.buffer_states[self.loaded_buffer_id] = state

    def _notify_path(self, message: str) -> None:
        """Show `message` as typed. A path can contain brackets."""
        self.notify(message, severity="warning", markup=False)

    def _resolve_file(self, path: Path) -> Union[Path, None]:
        try:
            resolved = path.expanduser().resolve()
            if resolved.is_dir():
                return None
        except OSError as exc:
            self._notify_path(f"Could not open {path.name}: {exc}")
            return None
        return resolved

    def _read_text_file(self, path: Path) -> Union[str, None]:
        try:
            data = path.read_bytes()
        except OSError as exc:
            self._notify_path(f"Could not open {path.name}: {exc}")
            return None
        if b"\x00" in data:
            self._notify_path(f"{path} is not a text file.")
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            self._notify_path(f"{path} is not a text file.")
            return None

    def _focus_open_path(self, path: Path) -> bool:
        """Focus the buffer already bound to `path`. True when one exists."""
        for buffer_id, state in self.buffer_states.items():
            if state.path != path:
                continue
            # the tab may still be mounting; activating it early raises.
            if self.tabs.get_tab(buffer_id) is not None and self.active != buffer_id:
                self.tabs.active = buffer_id
            self.editor.focus()
            return True
        return False

    def _can_reuse_blank(self) -> bool:
        """The only buffer is an untouched anonymous tab, so a file can take it."""
        if self.tab_count != 1 or self.loaded_buffer_id is None:
            return False
        state = self.buffer_states.get(self.loaded_buffer_id)
        if state is None:
            return False
        return state.path is None and state.text == "" and state.saved_text == ""

    def _load_file_into(self, buffer_id: str, path: Path, text: str) -> None:
        state = EditorState(text=text, path=path, saved_text=text)
        self.buffer_states[buffer_id] = state
        if self.loaded_buffer_id == buffer_id:
            self.editor.load_state(state)
        self._refresh_tab_label(buffer_id)
        self._update_tab_visibility()
        self.editor.focus()

    def _close_all_would_discard_work(self) -> bool:
        """True when close-all would drop text or an unsaved edit of a file."""
        self._save_loaded_buffer()
        for state in self.buffer_states.values():
            if state.text.strip():
                return True
            if state.path is not None and state.text != state.saved_text:
                return True
        return False

    def _loaded_file_is_dirty(self) -> bool:
        buffer_id = self.loaded_buffer_id
        if buffer_id is None:
            return False
        state = self.buffer_states.get(buffer_id)
        if state is None or state.path is None:
            return False
        return self.editor.text != state.saved_text

    def _refresh_tab_label(self, buffer_id: str) -> None:
        state = self.buffer_states.get(buffer_id)
        tab = self.tabs.get_tab(buffer_id)
        if state is None or tab is None:
            return
        if state.path is None:
            label = f"Tab {buffer_id.removeprefix('tab-')}"
        else:
            if buffer_id == self.loaded_buffer_id:
                text = self.editor.text
            else:
                text = state.text
            label = state.path.name
            if text != state.saved_text:
                label = f"{label} *"
        if tab.label_text != label:
            # A file name is literal text. "[dim]" in the name is not markup.
            tab.label = _literal_tab_label(label)
        if buffer_id == self.loaded_buffer_id:
            self._refresh_path_line()

    def _update_tab_visibility(self) -> None:
        """Show the tab bar when there is more than one buffer, or a named file.

        A single anonymous query stays unlabeled. A single open file would
        otherwise hide its name and unsaved mark.
        """
        named = any(state.path is not None for state in self.buffer_states.values())
        if self.tab_count > 1 or named:
            self.remove_class("hide-tabs")
        else:
            self.add_class("hide-tabs")
