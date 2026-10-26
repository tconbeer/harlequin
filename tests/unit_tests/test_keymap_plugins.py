"""The keymap plugins, and that a reload can drop only the bindings it added."""

from __future__ import annotations

from typing import cast

from textual.binding import Binding, BindingsMap
from textual.widget import Widget

from harlequin.actions import HARLEQUIN_ACTIONS
from harlequin.bindings import KEYMAP_BINDING_ID, bind, clear_keymap_bindings
from harlequin_keys import EMACS, FILES, VIM
from harlequin_vscode import VSCODE


class _Target:
    def __init__(self) -> None:
        self._bindings = BindingsMap([Binding("q", "quit", "Quit")])


def _pairs(keymap: object) -> set[tuple[str, object]]:
    bindings = keymap.bindings  # type: ignore[attr-defined]
    pairs: set[tuple[str, object]] = set()
    for binding in bindings:
        target = HARLEQUIN_ACTIONS[binding.action].target
        for key in binding.keys.split(","):
            pairs.add((key.strip(), target))
    return pairs


def test_files_keys_do_not_collide_with_vscode() -> None:
    assert _pairs(FILES).isdisjoint(_pairs(VSCODE))


def test_every_plugin_action_exists() -> None:
    for keymap in (FILES, VIM, EMACS):
        for binding in keymap.bindings:
            assert binding.action in HARLEQUIN_ACTIONS
    assert VIM.input_mode == "vim"
    assert EMACS.input_mode == "emacs"
    assert FILES.input_mode == "default"


def test_clear_keymap_bindings_drops_only_tagged_ids() -> None:
    target = _Target()
    widget = cast(Widget, target)
    bind(widget, "ctrl+shift+e", "show_files", description="Show Files", show=False)
    bind(widget, "ctrl+shift+e", "show_files", description="Show Files", show=False)
    target._bindings.key_to_bindings["f12"] = [
        Binding("f12", "show_debug_info", "Debug", id="kept")
    ]
    tagged = [
        binding for _, binding in target._bindings if binding.id == KEYMAP_BINDING_ID
    ]
    assert len(tagged) == 2
    clear_keymap_bindings(widget)
    remaining = {(key, binding.action, binding.id) for key, binding in target._bindings}
    assert remaining == {("q", "quit", None), ("f12", "show_debug_info", "kept")}
