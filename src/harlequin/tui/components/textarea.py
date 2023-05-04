from typing import NamedTuple

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


class Cursor(NamedTuple):
    lno: int
    pos: int


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

    lines: reactive[list[str]] = reactive(lambda: list(" "))
    cursor: reactive[Cursor] = reactive(Cursor(0, 0))
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

        if event.key == "right":
            event.stop()
            max_x = len(self.lines[self.cursor.lno]) - 1
            max_y = len(self.lines) - 1
            if self.cursor.lno == max_y:
                self.cursor = Cursor(lno=max_y, pos=min(max_x, self.cursor.pos + 1))
            elif self.cursor.pos == max_x:
                self.cursor = Cursor(lno=self.cursor.lno + 1, pos=0)
            else:
                self.cursor = Cursor(lno=self.cursor.lno, pos=self.cursor.pos + 1)
        elif event.key == "down":
            event.stop()
            max_y = len(self.lines) - 1
            if self.cursor.lno == max_y:
                self.cursor = Cursor(
                    lno=max_y, pos=len(self.lines[self.cursor.lno]) - 1
                )
            else:
                max_x = len(self.lines[self.cursor.lno + 1]) - 1
                self.cursor = Cursor(
                    lno=self.cursor.lno + 1, pos=min(max_x, self.cursor.pos)
                )
        elif event.key == "left":
            event.stop()
            if self.cursor.lno == 0:
                self.cursor = Cursor(0, pos=max(0, self.cursor.pos - 1))
            elif self.cursor.pos == 0:
                self.cursor = Cursor(
                    lno=self.cursor.lno - 1,
                    pos=len(self.lines[self.cursor.lno - 1]) - 1,
                )
            else:
                self.cursor = Cursor(lno=self.cursor.lno, pos=self.cursor.pos - 1)
        elif event.key == "up":
            event.stop()
            if self.cursor.lno == 0:
                self.cursor = Cursor(0, 0)
            else:
                max_x = len(self.lines[self.cursor.lno - 1]) - 1
                self.cursor = Cursor(
                    lno=self.cursor.lno - 1, pos=min(max_x, self.cursor.pos)
                )
        elif event.key == "home":
            self.cursor = Cursor(self.cursor.lno, 0)
        elif event.key == "end":
            self.cursor = Cursor(self.cursor.lno, len(self.lines[self.cursor.lno]) - 1)
        elif event.key == "ctrl+home":
            self.cursor = Cursor(0, 0)
        elif event.key == "ctrl+end":
            self.cursor = Cursor(lno=len(self.lines) - 1, pos=len(self.lines[-1]) - 1)
        elif event.key == "enter":
            event.stop()
            old_line = self.lines[self.cursor.lno]
            head = f"{old_line[:self.cursor.pos]} "
            tail = f"{old_line[self.cursor.pos:]}"
            self.lines[self.cursor.lno : self.cursor.lno + 1] = [head, tail]
            self.cursor = Cursor(self.cursor.lno + 1, 0)
        elif event.key == "delete":
            event.stop()
            old_line = self.lines[self.cursor.lno]
            if self.cursor.pos == len(old_line) - 1:
                # at the end of a line
                if self.cursor.lno == len(self.lines) - 1:
                    return  # EOF, nothing to do
                next_line = self.lines[self.cursor.lno + 1]
                new_line = f"{old_line.rstrip()}{next_line}"
                self.lines[self.cursor.lno : self.cursor.lno + 2] = [new_line]
            else:
                self.lines[
                    self.cursor.lno
                ] = f"{old_line[:self.cursor.pos]}{old_line[self.cursor.pos+1:]}"
        elif event.key == "backspace":
            event.stop()
            this_line = self.lines[self.cursor.lno]
            if self.cursor.pos == 0:
                if self.cursor.lno == 0:
                    return
                prev_line = self.lines[self.cursor.lno - 1]
                self.lines[self.cursor.lno - 1 : self.cursor.lno + 1] = [
                    f"{prev_line.rstrip()}{this_line}"
                ]
                self.cursor = Cursor(self.cursor.lno - 1, len(prev_line) - 1)
            else:
                self.lines[
                    self.cursor.lno
                ] = f"{this_line[:self.cursor.pos-1]}{this_line[self.cursor.pos:]}"
                self.cursor = Cursor(self.cursor.lno, self.cursor.pos - 1)
        elif event.key in (
            "apostrophe",
            "quotation_mark",
            "left_parenthesis",
            "left_square_bracket",
            "left_curly_bracket",
            "right_parenthesis",
            "right_square_bracket",
            "right_curly_bracket",
        ):
            assert event.character
            self._insert_closed_character_at_cursor(event.character)

        elif event.is_printable:
            event.stop()
            assert event.character is not None
            self._insert_character_at_cursor(event.character)

        self.update(self._content)

    def watch_cursor(self) -> None:
        self._scroll_to_cursor()

    @property
    def _content(self) -> RenderableType:
        syntax = Syntax("\n".join(self.lines), "sql")
        if self.cursor_visible:
            syntax.stylize_range(
                "reverse",
                (self.cursor.lno + 1, self.cursor.pos),
                (self.cursor.lno + 1, self.cursor.pos + 1),
            )
        return syntax

    def _scroll_to_cursor(self) -> None:
        self.post_message(self.CursorMoved(self.cursor.pos, self.cursor.lno))

    def _toggle_cursor(self) -> None:
        self.cursor_visible = not self.cursor_visible
        self.update(self._content)

    def _insert_character_at_cursor(self, character: str) -> None:
        line = self.lines[self.cursor.lno]
        new_line = f"{line[:self.cursor.pos]}{character}{line[self.cursor.pos:]}"
        self.lines[self.cursor.lno] = new_line
        self.cursor = Cursor(self.cursor.lno, self.cursor.pos + 1)

    def _insert_closed_character_at_cursor(self, character: str) -> None:
        closers = {
            '"': '"',
            "'": "'",
            "(": ")",
            "[": "]",
            "{": "}",
        }
        if self._get_character_at_cursor() == character:
            self.cursor = Cursor(self.cursor.lno, self.cursor.pos + 1)
        else:
            prev = self._get_character_before_cursor()
            self._insert_character_at_cursor(character)
            if (
                character in closers
                and self.cursor.pos == len(self.lines[self.cursor.lno]) - 1
                and (prev is None or prev == " ")
            ):
                self._insert_character_at_cursor(closers[character])
                self.cursor = Cursor(self.cursor.lno, self.cursor.pos - 1)

    def _get_character_at_cursor(self) -> str:
        return self.lines[self.cursor.lno][self.cursor.pos]

    def _get_character_before_cursor(self) -> str | None:
        if self.cursor.pos == 0:
            return None
        else:
            return self.lines[self.cursor.lno][self.cursor.pos - 1]

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
        self.cursor = Cursor(safe_y, min(max_x, x))
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
        input = self.query_one(TextInput)
        input.cursor_visible = True
        input.blink_timer.reset()
        input.move_cursor(event.x - 1, event.y)
        input.focus()

    def on_text_input_cursor_moved(self, event: TextInput.CursorMoved) -> None:
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
