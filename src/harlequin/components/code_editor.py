import re
from typing import List, Union

from rich.text import TextType
from sqlfmt.api import Mode, format_string
from sqlfmt.exception import SqlfmtError
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import ContentSwitcher, TabbedContent, TabPane, Tabs
from textual_textarea import TextArea, TextAreaSaved
from textual_textarea.key_handlers import Cursor
from textual_textarea.serde import serialize_lines
from textual_textarea.textarea import TextInput

from harlequin.cache import BufferState, load_cache
from harlequin.components.error_modal import ErrorModal


class CodeEditor(TextArea):
    BINDINGS = [
        Binding(
            "ctrl+enter",
            "submit",
            "Run Query",
            key_display="CTRL+ENTER / CTRL+J",
            show=True,
        ),
        Binding("ctrl+j", "submit", "Run Query", show=False),
        Binding("f4", "format", "Format Query", show=True),
    ]

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

        before = Cursor(0, 0)
        after: Union[None, Cursor] = None
        for c in semicolons:
            if c <= self.cursor:
                before = c
            elif after is None and c > self.cursor:
                after = c
                break
        else:
            after = Cursor(
                len(self.text_input.lines) - 1, len(self.text_input.lines[-1]) - 1
            )
        lines, first, last = self.text_input._get_selected_lines(before, after)
        lines[-1] = lines[-1][: last.pos]
        lines[0] = lines[0][first.pos :]
        return serialize_lines(lines)

    def on_mount(self) -> None:
        self.post_message(EditorCollection.EditorSwitched(active_editor=self))
        self.has_shown_clipboard_error = False

    def on_unmount(self) -> None:
        self.post_message(EditorCollection.EditorSwitched(active_editor=None))

    def on_text_area_saved(self, message: TextAreaSaved) -> None:
        self.app.notify(f"Editor contents saved to {message.path}")

    def on_text_area_clipboard_error(self) -> None:
        if not self.has_shown_clipboard_error:
            self.app.notify(
                "Could not access system clipboard.\n"
                "See https://harlequin.sh/docs/troubleshooting#copying-and-pasting",
                severity="error",
                timeout=10,
            )
            self.has_shown_clipboard_error = True

    async def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))

    def action_format(self) -> None:
        text_input = self.query_one(TextInput)
        old_cursor = text_input.cursor

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
            text_input.move_cursor(old_cursor.pos, old_cursor.lno)
            text_input.update(text_input._content)

    @property
    def _semicolons(self) -> List[Cursor]:
        semicolons: List[Cursor] = []
        for i, line in enumerate(self.text_input.lines):
            for pos in [m.span()[1] for m in re.finditer(";", line)]:
                semicolons.append(Cursor(i, pos))
        return semicolons


class EditorCollection(TabbedContent):
    BINDINGS = [
        Binding("ctrl+n", "new_buffer", "New Tab", show=False),
        Binding("ctrl+w", "close_buffer", "Close Tab", show=False),
        Binding("ctrl+k", "next_buffer", "Next Tab", show=False),
    ]

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
        theme: str = "monokai",
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

    async def on_mount(self) -> None:
        self.add_class("hide-tabs")
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

    def on_focus(self) -> None:
        self.current_editor.focus()

    def on_tabbed_content_tab_activated(
        self, message: TabbedContent.TabActivated
    ) -> None:
        message.stop()
        self.post_message(self.EditorSwitched(active_editor=None))
        self.current_editor.focus()

    async def action_new_buffer(self, state: Union[BufferState, None] = None) -> None:
        self.counter += 1
        new_tab_id = f"tab-{self.counter}"
        editor = CodeEditor(
            id=f"buffer-{self.counter}", language=self.language, theme=self.theme
        )
        pane = TabPane(
            f"Tab {self.counter}",
            editor,
            id=new_tab_id,
        )
        await self.add_pane(pane)
        if state is not None:
            editor.text = state.text
            editor.cursor = state.cursor
            editor.selection_anchor = state.selection_anchor
        else:
            self.active = new_tab_id
            self.current_editor.focus()
        if self.counter > 1:
            self.remove_class("hide-tabs")

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
