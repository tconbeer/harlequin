"""Key handling for the vim and emacs editor modes.

The VS Code keymap does not use this. A mode runs only while that keymap is
the one the app loaded, and only for the query editor's text area. Every
other widget keeps the bindings of the keymap that is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

InputMode = Literal["default", "vim", "emacs"]
CommandTarget = Literal["text", "code", "app", "buffers"]

MAX_REPEAT = 99
"""A count in vim repeats a motion at most this many times."""


@dataclass(frozen=True)
class EditorCommand:
    """One existing Harlequin action a mode key expands to."""

    target: CommandTarget
    action: str


@dataclass
class EditorChord:
    """The editor mode's place between keystrokes. One object, replaced in full."""

    vim: Literal["insert", "normal", "visual"] = "insert"
    count: int = 0
    pending: str = ""


@dataclass(frozen=True)
class KeyResult:
    state: EditorChord
    commands: tuple[EditorCommand, ...] = ()
    repeat: int = 1
    handled: bool = False


def fresh_chord() -> EditorChord:
    """Insert mode, no count, no prefix. What a keymap switch starts from."""
    return EditorChord()


def handle_key(mode: InputMode, state: EditorChord, key: str) -> KeyResult:
    """What `key` does in `mode`. Unhandled keys fall through to the keymap."""
    if mode == "vim":
        return _vim(state, _alias(key))
    if mode == "emacs":
        return _emacs(state, _alias(key))
    return KeyResult(state)


def _alias(key: str) -> str:
    """The key name Textual delivers, plus the letter a shifted letter is."""
    shifted = {
        "shift+a": "A",
        "shift+g": "G",
        "shift+i": "I",
        "shift+t": "T",
        "shift+v": "V",
    }
    return shifted.get(key, key)


_MOTIONS = {
    "h": "cursor_left",
    "j": "cursor_down",
    "k": "cursor_up",
    "l": "cursor_right",
    "w": "cursor_word_right",
    "b": "cursor_word_left",
    "0": "cursor_line_start",
    "dollar_sign": "cursor_line_end",
    "G": "cursor_doc_end",
    "ctrl+f": "cursor_page_down",
    "ctrl+b": "cursor_page_up",
    "ctrl+d": "cursor_page_down",
    "ctrl+u": "cursor_page_up",
}

_SPACE: dict[str, EditorCommand] = {
    "q": EditorCommand("app", "run_query"),
    "w": EditorCommand("code", "save"),
    "s": EditorCommand("code", "save"),
    "e": EditorCommand("app", "focus_query_editor"),
    "r": EditorCommand("app", "focus_results_viewer"),
    "c": EditorCommand("app", "focus_data_catalog"),
    "h": EditorCommand("app", "show_query_history"),
    "x": EditorCommand("app", "export"),
    "n": EditorCommand("buffers", "new_buffer"),
    "d": EditorCommand("buffers", "close_buffer"),
    "o": EditorCommand("app", "open_file"),
    "f": EditorCommand("app", "open_folder"),
    "F": EditorCommand("app", "show_files"),
    "b": EditorCommand("app", "toggle_sidebar"),
    "t": EditorCommand("buffers", "next_buffer"),
    "T": EditorCommand("buffers", "previous_buffer"),
    "g": EditorCommand("code", "goto_line"),
    "slash": EditorCommand("code", "find"),
    "question_mark": EditorCommand("app", "show_help_screen"),
}

_EMACS: dict[str, EditorCommand] = {
    "s": EditorCommand("code", "save"),
    "ctrl+s": EditorCommand("code", "save"),
    "c": EditorCommand("app", "quit"),
    "ctrl+c": EditorCommand("app", "quit"),
    "f": EditorCommand("app", "open_file"),
    "ctrl+f": EditorCommand("app", "open_file"),
    "k": EditorCommand("buffers", "close_buffer"),
    "ctrl+k": EditorCommand("buffers", "close_buffer"),
    "b": EditorCommand("buffers", "next_buffer"),
    "n": EditorCommand("buffers", "new_buffer"),
    "p": EditorCommand("buffers", "previous_buffer"),
    "e": EditorCommand("app", "export"),
    "h": EditorCommand("text", "select_all"),
    "u": EditorCommand("text", "undo"),
    "r": EditorCommand("text", "redo"),
}


def _text(action: str) -> EditorCommand:
    return EditorCommand("text", action)


def _done(state: EditorChord, *commands: EditorCommand, repeat: int = 1) -> KeyResult:
    return KeyResult(
        replace(state, count=0, pending=""),
        commands=commands,
        repeat=max(1, min(repeat, MAX_REPEAT)),
        handled=True,
    )


def _swallow(state: EditorChord) -> KeyResult:
    """A normal-mode key that is not a command. It must not insert text."""
    return KeyResult(replace(state, count=0, pending=""), handled=True)


def _motion(state: EditorChord, action: str) -> KeyResult:
    if state.vim == "visual":
        action = f"{action}(True)"
    repeat = state.count or 1
    if action.startswith("cursor_doc_end") and state.count:
        # `3G` is line 3: the top of the buffer, then two lines down.
        selecting = state.vim == "visual"
        line = _text("cursor_doc_start(True)" if selecting else "cursor_doc_start")
        down = _text("cursor_down(True)" if selecting else "cursor_down")
        steps = max(0, min(state.count, MAX_REPEAT) - 1)
        return KeyResult(
            replace(state, count=0, pending=""),
            commands=(line, *([down] * steps)),
            repeat=1,
            handled=True,
        )
    return _done(state, _text(action), repeat=repeat)


def _vim(state: EditorChord, key: str) -> KeyResult:
    if state.vim == "insert":
        if key == "escape":
            return KeyResult(
                replace(state, vim="normal", count=0, pending=""), handled=True
            )
        return KeyResult(state)

    if state.pending == "space":
        command = _SPACE.get(key)
        if command is None:
            return _swallow(state)
        return _done(state, command)

    if state.pending == "g":
        if key == "g":
            repeat = state.count or 1
            if repeat == 1:
                return _motion(replace(state, count=0), "cursor_doc_start")
            return _motion(replace(state, pending=""), "cursor_doc_end")
        return _vim(replace(state, pending=""), key)

    if state.pending == "d":
        deletes = {
            "d": "delete_line",
            "w": "delete_word_right",
            "dollar_sign": "delete_to_end_of_line",
            "0": "delete_to_start_of_line",
        }
        action = deletes.get(key)
        if action is None:
            return _swallow(state)
        return _done(state, _text(action), repeat=state.count or 1)

    if state.pending == "y":
        if key == "y":
            return _done(state, _text("select_line"), _text("copy"))
        if key == "w":
            return _done(state, _text("select_word"), _text("copy"))
        return _swallow(state)

    if key == "escape":
        return KeyResult(
            replace(state, vim="normal", count=0, pending=""), handled=True
        )
    if key.isdigit() and not (key == "0" and state.count == 0):
        count = min(state.count * 10 + int(key), MAX_REPEAT)
        return KeyResult(replace(state, count=count), handled=True)
    if key in _MOTIONS and not (key == "G" and state.count):
        return _motion(state, _MOTIONS[key])
    if key == "G" and state.count:
        return _motion(state, "cursor_doc_end")
    if key == "g":
        return KeyResult(replace(state, pending="g"), handled=True)
    if key == "d":
        return KeyResult(replace(state, pending="d"), handled=True)
    if key == "y":
        return KeyResult(replace(state, pending="y"), handled=True)
    if key == "space":
        return KeyResult(replace(state, pending="space"), handled=True)
    if key == "x":
        return _done(state, _text("delete_right"), repeat=state.count or 1)
    if key == "p":
        return _done(state, _text("paste"), repeat=state.count or 1)
    if key == "u":
        return _done(state, _text("undo"), repeat=state.count or 1)
    if key == "ctrl+r":
        return _done(state, _text("redo"))
    if key == "slash":
        return _done(state, EditorCommand("code", "find"))
    if key == "n":
        return _done(state, EditorCommand("code", "find(True)"))
    if key == "i":
        return KeyResult(
            replace(state, vim="insert", count=0, pending=""), handled=True
        )
    if key == "a":
        return KeyResult(
            replace(state, vim="insert", count=0, pending=""),
            commands=(_text("cursor_right"),),
            handled=True,
        )
    if key == "A":
        return KeyResult(
            replace(state, vim="insert", count=0, pending=""),
            commands=(_text("cursor_line_end"),),
            handled=True,
        )
    if key == "I":
        return KeyResult(
            replace(state, vim="insert", count=0, pending=""),
            commands=(_text("cursor_line_start"),),
            handled=True,
        )
    if key == "v":
        nxt: Literal["insert", "normal", "visual"] = (
            "normal" if state.vim == "visual" else "visual"
        )
        return KeyResult(replace(state, vim=nxt, count=0, pending=""), handled=True)
    if key == "V":
        return KeyResult(
            replace(state, vim="visual", count=0, pending=""),
            commands=(_text("select_line"),),
            handled=True,
        )
    return _swallow(state)


def _emacs(state: EditorChord, key: str) -> KeyResult:
    """`ctrl+x` starts a prefix. Every other key stays with the emacs keymap."""
    if state.pending == "ctrl+x":
        if key in {"escape", "ctrl+g"}:
            return KeyResult(replace(state, pending=""), handled=True)
        command = _EMACS.get(key)
        if command is None:
            return KeyResult(replace(state, pending=""), handled=True)
        return _done(state, command)
    if key == "ctrl+x":
        return KeyResult(replace(state, pending="ctrl+x"), handled=True)
    return KeyResult(state)
