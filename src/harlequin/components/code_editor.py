from __future__ import annotations

from typing import List, Union

from sqlfmt.api import Mode, format_string
from sqlfmt.exception import SqlfmtError
from textual.content import ContentType
from textual.css.query import InvalidQueryFormat, NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import ContentSwitcher, TabbedContent, TabPane, Tabs
from textual.widgets.text_area import Location, Selection
from textual_textarea import TextAreaSaved, TextEditor

from harlequin.autocomplete import MemberCompleter, WordCompleter
from harlequin.components.error_modal import ErrorModal
from harlequin.editor_cache import BufferState, load_cache
from harlequin.messages import WidgetMounted


class CodeEditor(TextEditor, inherit_bindings=False):
    SEMICOLON_QUERY = '(";" @semicolon)'

    class Submitted(Message, bubble=True):
        """Posted when user runs the query.

        Attributes:
            lines: The lines of code being submitted.
            cursor: The position of the cursor
        """

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def selected_queries(self) -> list[str]:
        """
        Returns the list of queries that intersect
        with the current selection.
        """
        if self.text_input is None or not self.text.strip():
            return []

        if ";" not in self.text:
            return [self.text]

        separators = self._query_separators()
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

    def on_mount(self) -> None:
        self.post_message(EditorCollection.EditorSwitched(active_editor=self))
        self.post_message(WidgetMounted(widget=self))
        self.has_shown_clipboard_error = False
        self.has_shown_tree_sitter_error = False
        self._semicolon_query = self.prepare_query(self.SEMICOLON_QUERY)

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

        try:
            self.text = format_string(self.text, Mode())
        except SqlfmtError as e:
            self.app.push_screen(
                ErrorModal(
                    title="Formatting Error",
                    header="There was an error while formatting your file:",
                    error=e,
                )
            )
        else:
            self.text_input.selection = old_selection

    def action_focus_results_viewer(self) -> None:
        if hasattr(self.app, "action_focus_results_viewer"):
            self.app.action_focus_results_viewer()

    def action_focus_data_catalog(self) -> None:
        if hasattr(self.app, "action_focus_data_catalog"):
            self.app.action_focus_data_catalog()

    def _query_separators(self) -> list[Location]:
        """
        Return a sorted list of tuples that represent the row and col
        positions of query separators (semicolons) in the buffer text.
        """
        if self.text_input is None:
            return []

        if self.text_input.is_syntax_aware:
            assert self._semicolon_query is not None
            query_result = self.query_syntax_tree(query=self._semicolon_query)
            # tree-sitter captures nodes in the order its patterns match them,
            # which is not the order they appear in the buffer.
            return sorted(
                (n.end_point.row, n.end_point.column)
                for n in query_result.get("semicolon", [])
            )

        else:
            # tree-sitter is not installed. naively split on semicolons and
            # show a warning.
            import re

            if not self.has_shown_tree_sitter_error:
                self.app.notify(
                    "Tree-sitter is not installed. Syntax highlighting and query "
                    "splitting may not work as expected.\n"
                    "See https://harlequin.sh/docs/troubleshooting/tree-sitter",
                    severity="warning",
                    timeout=10,
                )
                self.has_shown_tree_sitter_error = True

            semicolons: list[Location] = []
            for i, line in enumerate(self.text.splitlines()):
                for pos in [m.span()[1] for m in re.finditer(";", line)]:
                    semicolons.append((i, pos))

            return semicolons


class EditorCollection(TabbedContent):
    BORDER_TITLE = "Query Editor"
    theme: reactive[str] = reactive("harlequin")

    class EditorSwitched(Message):
        def __init__(self, active_editor: Union[CodeEditor, None]) -> None:
            self.active_editor = active_editor
            super().__init__()

    def __init__(
        self,
        *titles: ContentType,
        initial: str = "",
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa: A002
        classes: Union[str, None] = None,
        disabled: bool = False,
        language: str = "sql",
        theme: str = "harlequin",
    ):
        super().__init__(
            *titles,
            initial=initial,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self.language = language
        self.theme = theme
        self.counter = 0
        self._word_completer: WordCompleter | None = None
        self._member_completer: MemberCompleter | None = None
        self.startup_cache = load_cache()

    @property
    def current_editor(self) -> CodeEditor:
        content = self.query_one(ContentSwitcher)
        active_tab_id = self.active
        if active_tab_id:
            try:
                tab_pane = content.query_one(f"#{active_tab_id}", TabPane)
                return tab_pane.query_one(CodeEditor)
            except (NoMatches, InvalidQueryFormat):
                pass
        all_editors = content.query(CodeEditor)
        return all_editors.first(CodeEditor)

    @property
    def all_editors(self) -> List[CodeEditor]:
        try:
            content = self.query_one(ContentSwitcher)
            all_editors = content.query(CodeEditor)
        except NoMatches:
            return []
        return list(all_editors)

    @property
    def member_completer(self) -> MemberCompleter | None:
        return self._member_completer

    @member_completer.setter
    def member_completer(self, new_completer: MemberCompleter) -> None:
        self._member_completer = new_completer
        try:
            self.current_editor.member_completer = new_completer
        except NoMatches:
            pass

    @property
    def word_completer(self) -> WordCompleter | None:
        return self._word_completer

    @word_completer.setter
    def word_completer(self, new_completer: WordCompleter) -> None:
        self._word_completer = new_completer
        try:
            self.current_editor.word_completer = new_completer
        except NoMatches:
            pass

    async def on_mount(self) -> None:
        if self.startup_cache is not None:
            for _i, buffer in enumerate(self.startup_cache.buffers):
                await self.action_new_buffer(state=buffer)
                # we can't load the focus state here, since Tabs
                # really wants to activate the first tab when it's
                # mounted
        else:
            await self.action_new_buffer()
        self.query_one(Tabs).can_focus = False
        self.current_editor.word_completer = self.word_completer
        self.current_editor.member_completer = self.member_completer
        self.remove_class("premount")
        self.post_message(WidgetMounted(widget=self))

    def on_focus(self) -> None:
        self.current_editor.focus()

    def on_tabbed_content_tab_activated(
        self, message: TabbedContent.TabActivated
    ) -> None:
        message.stop()
        self.post_message(self.EditorSwitched(active_editor=None))
        self.current_editor.word_completer = self.word_completer
        self.current_editor.member_completer = self.member_completer
        self.current_editor.focus()

    def watch_theme(self, theme: str) -> None:
        for editor in self.all_editors:
            editor.theme = theme

    async def insert_buffer_with_text(self, query_text: str) -> None:
        state = BufferState(selection=Selection(), text=query_text)
        new_editor = await self.action_new_buffer(state=state)
        new_editor.focus()

    async def action_new_buffer(
        self, state: Union[BufferState, None] = None
    ) -> CodeEditor:
        self.counter += 1
        new_tab_id = f"tab-{self.counter}"
        editor = CodeEditor(
            id=f"buffer-{self.counter}",
            text=state.text if state is not None else "",
            language=self.language,
            theme=self.theme,
            word_completer=self.word_completer,
            member_completer=self.member_completer,
        )
        # The trailing ✕ is a clickable close button (Textual @click markup);
        # clicking it closes this specific tab, clicking the label selects it.
        pane = TabPane(
            f"Tab {self.counter} [@click=app.close_editor_tab('{new_tab_id}')]✕[/]",
            editor,
            id=new_tab_id,
        )
        await self.add_pane(pane)
        if state is not None:
            editor.selection = state.selection
        else:
            self.active = new_tab_id
            try:
                self.current_editor.focus()
            except NoMatches:
                pass
        if self.counter > 1:
            self.remove_class("hide-tabs")
        return editor

    def action_close_buffer(self) -> None:
        if self.tab_count > 1:
            if self.tab_count == 2:
                self.add_class("hide-tabs")
            self.remove_pane(self.active)
        else:
            self.current_editor.text = ""
            self.current_editor.cursor = (0, 0)  # type: ignore
        self.current_editor.focus()

    def close_buffer_by_id(self, tab_id: str) -> None:
        """Close a specific tab (used by the clickable ✕ on each tab)."""
        if self.tab_count <= 1:
            self.current_editor.text = ""
            self.current_editor.cursor = (0, 0)  # type: ignore
            return
        if self.tab_count == 2:
            self.add_class("hide-tabs")
        # If we're closing the active tab, move focus to another tab FIRST so the
        # TabbedContent doesn't try to re-activate the tab we're about to remove
        # (that raises "No Tab with id ..."). This is what makes closing a
        # non-active/restored tab work, not just the active one.
        if self.active == tab_id:
            other = next(
                (
                    pane.id
                    for pane in self.query(TabPane)
                    if pane.id and pane.id != tab_id
                ),
                None,
            )
            if other is not None:
                self.active = other
        self.remove_pane(tab_id)
        try:
            self.current_editor.focus()
        except NoMatches:
            pass

    def action_next_buffer(self) -> None:
        active = self.active
        if self.tab_count < 2 or active is None:
            return
        tabs = self.query(TabPane)
        next_tabs = tabs[1:]
        next_tabs.append(tabs[0])
        lookup = {t.id: nt.id for t, nt in zip(tabs, next_tabs, strict=False)}
        self.active = lookup[active]  # type: ignore
        self.post_message(self.EditorSwitched(active_editor=None))
        self.current_editor.focus()
