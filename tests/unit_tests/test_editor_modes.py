"""The vim and emacs editor modes, without a terminal."""

from __future__ import annotations

from harlequin.editor_modes import EditorChord, InputMode, fresh_chord, handle_key


def _press(
    mode: InputMode, state: EditorChord, *keys: str
) -> tuple[EditorChord, list[str]]:
    actions: list[str] = []
    for key in keys:
        result = handle_key(mode, state, key)
        state = result.state
        actions.extend(command.action for command in result.commands)
    return state, actions


def test_escape_enters_normal_and_j_moves_down() -> None:
    state, actions = _press("vim", fresh_chord(), "escape", "j")
    assert state.vim == "normal"
    assert actions == ["cursor_down"]


def test_i_returns_to_insert_and_a_letter_is_not_handled() -> None:
    state, actions = _press("vim", fresh_chord(), "escape", "i")
    assert state.vim == "insert"
    assert actions == []
    result = handle_key("vim", state, "j")
    assert result.handled is False
    assert result.commands == ()
    assert result.state.vim == "insert"


def test_three_j_asks_for_three_downs() -> None:
    state = fresh_chord()
    state, _ = _press("vim", state, "escape")
    result = handle_key("vim", state, "3")
    result = handle_key("vim", result.state, "j")
    assert result.repeat == 3
    assert [command.action for command in result.commands] == ["cursor_down"]


def test_gg_goes_to_the_start_of_the_buffer() -> None:
    _, actions = _press("vim", fresh_chord(), "escape", "g", "g")
    assert actions == ["cursor_doc_start"]


def test_dd_deletes_the_line() -> None:
    _, actions = _press("vim", fresh_chord(), "escape", "d", "d")
    assert actions == ["delete_line"]


def test_space_q_runs_the_query() -> None:
    _, actions = _press("vim", fresh_chord(), "escape", "space", "q")
    assert actions == ["run_query"]


def test_insert_mode_j_is_not_handled() -> None:
    result = handle_key("vim", fresh_chord(), "j")
    assert result.handled is False


def test_emacs_ctrl_x_k_closes_the_buffer() -> None:
    _, actions = _press("emacs", fresh_chord(), "ctrl+x", "k")
    assert actions == ["close_buffer"]


def test_an_unknown_emacs_prefix_is_cleared() -> None:
    result = handle_key("emacs", fresh_chord(), "ctrl+x")
    assert result.state.pending == "ctrl+x"
    result = handle_key("emacs", result.state, "z")
    assert result.handled is True
    assert result.commands == ()
    assert result.state.pending == ""


def test_a_count_is_capped() -> None:
    state = EditorChord(vim="normal")
    for key in "999":
        result = handle_key("vim", state, key)
        state = result.state
    assert state.count == 99
