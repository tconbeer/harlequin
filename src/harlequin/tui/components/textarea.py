from typing import List, Union

from rich.console import RenderableType
from rich.syntax import Syntax
from sqlfmt.api import Mode, format_string
from sqlfmt.exception import SqlfmtError
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from harlequin.tui.components.error_modal import ErrorModal
from harlequin.tui.components.filename_modal import FilenameModal
from harlequin.tui.components.key_handlers import Cursor, handle_arrow

BRACKETS = {
    "(": ")",
    "[": "]",
    "{": "}",
}
CLOSERS = {'"': '"', "'": "'", **BRACKETS}


class TextInput(Static, can_focus=True):
    BINDINGS = [
        ("ctrl+`", "format", "Format Query"),
        Binding("ctrl+@", "format", "Format Query", show=False),
        ("ctrl+s", "save", "Save Query"),
        ("ctrl+o", "load", "Open Query"),
    ]

    DEFAULT_CSS = """
    TextInput{
        height: auto;
        width: auto;
        padding: 0 1;
    }
    """

    lines: reactive[List[str]] = reactive(lambda: list(" "))
    cursor: reactive[Cursor] = reactive(Cursor(0, 0))
    selection_anchor: reactive[Union[Cursor, None]] = reactive(None)
    cursor_visible: reactive[bool] = reactive(True)

    class CursorMoved(Message, bubble=True):
        """Posted when the cursor moves

        Attributes:
            cursor_x: The x position of the cursor
            cursor_y: The y position
        """

        def __init__(self, cursor_x: int, cursor_y: int) -> None:
            super().__init__()
            self.cursor_x = cursor_x
            self.cursor_y = cursor_y

    def on_mount(self) -> None:
        self.blink_timer = self.set_interval(
            0.5,
            self._toggle_cursor,
            pause=not self.has_focus,
        )

    def on_focus(self) -> None:
        self.cursor_visible = True
        self.blink_timer.reset()
        self._scroll_to_cursor()
        self.update(self._content)

    def on_blur(self) -> None:
        self.blink_timer.pause()
        self.cursor_visible = False
        self.update(self._content)

    def on_key(self, event: events.Key) -> None:
        self.cursor_visible = True
        self.blink_timer.reset()
        selection_before = self.selection_anchor

        # set selection_anchor if it's unset
        if event.key == "shift+delete":
            pass  # todo: shift+delete should delete the whole line
        elif event.key == "ctrl+a":
            self.selection_anchor = Cursor(0, 0)
        elif selection_before is None and "shift" in event.key:
            self.selection_anchor = self.cursor
        elif selection_before is not None and "shift" not in event.key:
            self.selection_anchor = None

        # set cursor and modify lines if necessary
        if event.key in (
            "apostrophe",
            "quotation_mark",
            "left_parenthesis",
            "left_square_bracket",
            "left_curly_bracket",
            "right_parenthesis",
            "right_square_bracket",
            "right_curly_bracket",
        ):
            assert event.character is not None
            self.log(f"here! {event.character}")
            if selection_before is None:
                self._insert_closed_character_at_cursor(event.character, self.cursor)
            elif event.key in (
                "right_parenthesis",
                "right_square_bracket",
                "right_curly_bracket",
            ):
                self._delete_selection(selection_before, self.cursor)
                self._insert_character_at_cursor(event.character, self.cursor)
                self.cursor = Cursor(
                    lno=self.cursor.lno, pos=self.cursor.pos + len(event.character)
                )
            else:
                self._insert_characters_around_selection(
                    event.character, selection_before, self.cursor
                )
        elif event.key in ("pageup", "shift+pageup"):
            event.stop()
            self.move_cursor(
                x=self.cursor.pos, y=(self.cursor.lno - self._visible_height() + 1)
            )
        elif event.key in ("pagedown", "shift+pagedown"):
            event.stop()
            self.move_cursor(
                x=self.cursor.pos, y=(self.cursor.lno + self._visible_height() - 1)
            )
        elif any([dir in event.key for dir in ["left", "right", "up", "down"]]):
            event.stop()
            self.cursor = handle_arrow(event.key, self.lines, self.cursor)
        elif event.key in ("home", "shift+home"):
            event.stop()
            self.cursor = Cursor(self.cursor.lno, 0)
        elif event.key in ("end", "shift+end"):
            event.stop()
            self.cursor = Cursor(self.cursor.lno, len(self.lines[self.cursor.lno]) - 1)
        elif event.key == "ctrl+home":
            self.cursor = Cursor(0, 0)
        elif event.key in ("ctrl+end", "ctrl+a"):
            self.cursor = Cursor(lno=len(self.lines) - 1, pos=len(self.lines[-1]) - 1)
        elif event.key == "enter":
            event.stop()
            anchor = selection_before or self.cursor
            first = min(anchor, self.cursor)
            last = max(anchor, self.cursor)
            old_lines = self.lines[first.lno : last.lno + 1]
            head = f"{old_lines[0][:first.pos]} "
            tail = f"{old_lines[-1][last.pos:]}"
            self.lines[first.lno : last.lno + 1] = [head, tail]
            self.cursor = Cursor(first.lno + 1, 0)
        elif event.key == "delete":
            event.stop()
            if selection_before is None:
                anchor = self.cursor
                cursor = handle_arrow("right", self.lines, self.cursor)
            else:
                anchor = selection_before
                cursor = self.cursor
            self._delete_selection(anchor, cursor)
        elif event.key == "backspace":
            event.stop()
            if selection_before is None:
                anchor = self.cursor
                cursor = handle_arrow("left", self.lines, self.cursor)
            else:
                anchor = selection_before
                cursor = self.cursor
            self._delete_selection(anchor, cursor)

        elif event.is_printable:
            event.stop()
            assert event.character is not None
            if selection_before is not None:
                self._delete_selection(selection_before, self.cursor)
            self._insert_character_at_cursor(event.character, self.cursor)
            self.cursor = Cursor(
                lno=self.cursor.lno, pos=self.cursor.pos + len(event.character)
            )

        self.update(self._content)

    def watch_cursor(self) -> None:
        self._scroll_to_cursor()

    @property
    def _content(self) -> RenderableType:
        syntax = Syntax("\n".join(self.lines), "sql")
        if self.cursor_visible:
            syntax.stylize_range(
                "reverse",
                # rows are 1-indexed
                (self.cursor.lno + 1, self.cursor.pos),
                (self.cursor.lno + 1, self.cursor.pos + 1),
            )
        if self.selection_anchor is not None:
            first = min(self.selection_anchor, self.cursor)
            second = max(self.selection_anchor, self.cursor)
            syntax.stylize_range(
                "on #666666",
                # rows are 1-indexed
                (first.lno + 1, first.pos),
                (second.lno + 1, second.pos),
            )
        return syntax

    def _scroll_to_cursor(self) -> None:
        self.post_message(self.CursorMoved(self.cursor.pos, self.cursor.lno))

    def _visible_height(self) -> int:
        parent = self.parent
        assert isinstance(parent, TextContainer)
        return parent.window_region.height

    def _toggle_cursor(self) -> None:
        self.cursor_visible = not self.cursor_visible
        self.update(self._content)

    def _insert_character_at_cursor(self, character: str, cursor: Cursor) -> None:
        line = self.lines[cursor.lno]
        new_line = f"{line[:cursor.pos]}{character}{line[cursor.pos:]}"
        self.lines[cursor.lno] = new_line

    def _insert_characters_around_selection(
        self, character: str, anchor: Cursor, cursor: Cursor
    ) -> None:
        first = min(anchor, cursor)
        last = max(anchor, cursor)
        self._insert_character_at_cursor(character, first)
        if first.lno == last.lno:
            self.cursor = Cursor(lno=last.lno, pos=last.pos + len(character))
        else:
            self.cursor = last
        self._insert_character_at_cursor(CLOSERS[character], self.cursor)

    def _insert_closed_character_at_cursor(
        self, character: str, cursor: Cursor
    ) -> None:
        if self._get_character_at_cursor(cursor) == character:
            self.cursor = Cursor(cursor.lno, cursor.pos + 1)
        else:
            prev = self._get_character_before_cursor(cursor)
            self._insert_character_at_cursor(character, cursor)
            self.cursor = Cursor(cursor.lno, cursor.pos + len(character))
            if (
                character in CLOSERS
                and self.cursor.pos == len(self.lines[self.cursor.lno]) - 1
                and (prev is None or prev == " " or character in BRACKETS)
            ):
                self._insert_character_at_cursor(CLOSERS[character], self.cursor)

    def _delete_selection(self, anchor: Cursor, cursor: Cursor) -> None:
        first = min(anchor, cursor)
        last = max(anchor, cursor)
        old_lines = self.lines[first.lno : last.lno + 1]
        head = f"{old_lines[0][:first.pos]}"
        tail = f"{old_lines[-1][last.pos:]}"
        self.lines[first.lno : last.lno + 1] = [f"{head}{tail}"]
        self.cursor = Cursor(first.lno, first.pos)

    def _get_character_at_cursor(self, cursor: Cursor) -> str:
        return self.lines[cursor.lno][cursor.pos]

    def _get_character_before_cursor(self, cursor: Cursor) -> Union[str, None]:
        if self.cursor.pos == 0:
            return None
        else:
            return self.lines[cursor.lno][cursor.pos - 1]

    def action_format(self) -> None:
        code = "\n".join(self.lines)
        try:
            formatted = format_string(code, Mode())
        except SqlfmtError as e:
            self.app.push_screen(
                ErrorModal(
                    title="Formatting Error",
                    header="There was an error while formatting your file:",
                    error=e,
                )
            )
        else:
            self.lines = [f"{line} " for line in formatted.splitlines()]
            self.move_cursor(self.cursor.pos, self.cursor.lno)
            self.update(self._content)

    def action_save(self) -> None:
        self.app.push_screen(FilenameModal(id="save_modal"))

    def action_load(self) -> None:
        self.app.push_screen(FilenameModal(id="load_modal"))

    def move_cursor(self, x: int, y: int) -> None:
        max_y = len(self.lines) - 1
        safe_y = min(max_y, y)
        max_x = len(self.lines[safe_y]) - 1
        safe_x = min(max_x, x)
        self.cursor = Cursor(lno=max(0, safe_y), pos=max(0, safe_x))
        self.update(self._content)


class TextContainer(
    ScrollableContainer,
    inherit_bindings=False,
    can_focus=False,
    can_focus_children=True,
):
    pass


class TextArea(Widget, can_focus=False, can_focus_children=True):
    def compose(self) -> ComposeResult:
        with TextContainer():
            yield TextInput()

    def on_mount(self) -> None:
        self.border_title = "Query Editor"

    def on_click(self, event: events.Click) -> None:
        """
        Moves the cursor to the click.
        """
        input = self.query_one(TextInput)
        input.cursor_visible = True
        input.blink_timer.reset()
        input.move_cursor(event.x - 1, event.y)
        input.focus()

    def on_text_input_cursor_moved(self, event: TextInput.CursorMoved) -> None:
        """
        Scrolls the container so the cursor is visible.
        """
        event.stop()
        container = self.query_one(TextContainer)
        x_buffer = container.window_region.width // 4
        y_buffer = container.window_region.height // 4
        if event.cursor_x < container.window_region.x + x_buffer:  # scroll left
            container.scroll_to(event.cursor_x - x_buffer, container.window_region.y)
        elif (
            event.cursor_x
            >= container.window_region.x + container.window_region.width - x_buffer
        ):  # scroll right
            container.scroll_to(
                event.cursor_x - container.window_region.width + x_buffer,
                container.window_region.y,
            )
        if event.cursor_y < container.window_region.y + y_buffer:  # scroll up
            container.scroll_to(container.window_region.x, event.cursor_y - y_buffer)
        elif (
            event.cursor_y
            >= container.window_region.y + container.window_region.height - y_buffer
        ):  # scroll down
            container.scroll_to(
                container.window_region.x,
                event.cursor_y - container.window_region.height + y_buffer,
            )


if __name__ == "__main__":
    from textual.app import App, ComposeResult

    class TextApp(App):
        def compose(self) -> ComposeResult:
            yield TextArea()

        def on_mount(self) -> None:
            ta = self.query_one(TextArea)
            ta.focus()

    app = TextApp()
    app.run()
