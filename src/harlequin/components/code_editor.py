from __future__ import annotations

import re
from typing import List, Union

from rich.text import TextType
from sqlfmt.api import Mode, format_string
from sqlfmt.exception import SqlfmtError
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import ContentSwitcher, TabbedContent, TabPane, Tabs
from textual_textarea import TextAreaSaved, TextEditor

from harlequin.autocomplete import MemberCompleter, WordCompleter
from harlequin.components.error_modal import ErrorModal
from harlequin.editor_cache import BufferState, load_cache
from harlequin.messages import WidgetMounted


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

    @property
    def current_query(self) -> str:
        semicolons = self._semicolons

        if not semicolons:
            return self.text

        before = (0, 0)
        after: Union[None, tuple[int, int]] = None
        for c in semicolons:
            if c <= self.selection.end:
                before = c
            elif after is None and c > self.selection.end:
                after = c
                break
        else:
            lno = self.text_input.document.line_count - 1
            after = (lno, len(self.text_input.document.get_line(lno)))
        return self.text_input.get_text_range(
            start=(before[0], before[1]), end=(after[0], after[1])
        ).strip()

    @property
    def previous_query(self) -> str:
        semicolons = self._semicolons

        if not semicolons:
            return self.text

        first = (0, 0)
        second = (0, 0)
        for c in semicolons:
            if c <= self.selection.end:
                first = second
                second = c
            elif c > self.selection.end:
                break

        return self.text_input.get_text_range(
            start=(first[0], first[1]), end=(second[0], second[1])
        ).strip()

    def on_mount(self) -> None:
        self.post_message(EditorCollection.EditorSwitched(active_editor=self))
        self.post_message(WidgetMounted(widget=self))
        self.has_shown_clipboard_error = False

    def on_unmount(self) -> None:
        self.post_message(EditorCollection.EditorSwitched(active_editor=None))

    def on_text_area_saved(self, message: TextAreaSaved) -> None:
        self.app.notify(f"Editor contents saved to {message.path}")

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

    @property
    def _semicolons(self) -> list[tuple[int, int]]:
        semicolons: list[tuple[int, int]] = []
        for i, line in enumerate(self.text.splitlines()):
            for pos in [m.span()[1] for m in re.finditer(";", line)]:
                semicolons.append((i, pos))
        return semicolons


class EditorCollection(TabbedContent):

    BORDER_TITLE = "Query Editor"

    class EditorSwitched(Message):
        def __init__(self, active_editor: Union[CodeEditor, None]) -> None:
            self.active_editor = active_editor
            super().__init__()

    def __init__(
        self,
        *titles: TextType,
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

    @property
    def current_editor(self) -> CodeEditor:
        content = self.query_one(ContentSwitcher)
        active_tab_id = self.active
        if active_tab_id:
            try:
                tab_pane = content.query_one(f"#{active_tab_id}", TabPane)
                return tab_pane.query_one(CodeEditor)
            except NoMatches:
                pass
        all_editors = content.query(CodeEditor)
        return all_editors.first(CodeEditor)

    @property
    def all_editors(self) -> List[CodeEditor]:
        content = self.query_one(ContentSwitcher)
        all_editors = content.query(CodeEditor)
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
        cache = load_cache()
        if cache is not None:
            for _i, buffer in enumerate(cache.buffers):
                await self.action_new_buffer(state=buffer)
                # we can't load the focus state here, since Tabs
                # really wants to activate the first tab when it's
                # mounted
        else:
            await self.action_new_buffer()
        self.query_one(Tabs).can_focus = False
        self.current_editor.word_completer = self.word_completer
        self.current_editor.member_completer = self.member_completer
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

    async def insert_buffer_with_text(self, query_text: str) -> None:
        new_editor = await self.action_new_buffer()
        new_editor.text = query_text
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
        pane = TabPane(
            f"Tab {self.counter}",
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

    def action_next_buffer(self) -> None:
        active = self.active
        if self.tab_count < 2 or active is None:
            return
        tabs = self.query(TabPane)
        next_tabs = tabs[1:]
        next_tabs.append(tabs[0])
        lookup = {t.id: nt.id for t, nt in zip(tabs, next_tabs)}
        self.active = lookup[active]  # type: ignore
        self.post_message(self.EditorSwitched(active_editor=None))
        self.current_editor.focus()
