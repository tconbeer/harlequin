"""Which keys a binding has to claim ahead of the focused widget.

A key that reaches Harlequin carrying a printable character is inserted by a
focused Input or TextArea before any binding on it fires, so `bind()` derives
priority from the key. What is asserted here is the split: alt+<character> is
claimed, and neither an unmodified key nor a ctrl+ key is.
"""

from __future__ import annotations

import pytest
from textual.widget import Widget

from harlequin.bindings import bind, key_needs_priority


@pytest.mark.parametrize(
    "key",
    [
        "alt+e",
        "alt+1",
        "alt+shift+e",
    ],
)
def test_keys_that_arrive_as_text(key: str) -> None:
    assert key_needs_priority(key) is True


@pytest.mark.parametrize(
    "key",
    [
        # typing looks exactly like this, so these keep Textual's behavior
        "e",
        "space",
        "full_stop",
        # ctrl replaces the character with a control code
        "ctrl+e",
        "ctrl+alt+e",
        # a terminal never prefixes a named key with alt+
        "alt+left",
        "alt+enter",
        # no character at all
        "f4",
        "shift+tab",
        # degenerate spellings must not raise
        "",
        "+",
    ],
)
def test_keys_that_do_not_arrive_as_text(key: str) -> None:
    assert key_needs_priority(key) is False


def test_priority_is_derived_per_key() -> None:
    """One binding over two keys splits, so ctrl+e keeps bubbling."""
    widget = Widget()
    bind(widget, "alt+e,ctrl+e", "launch_external_editor")

    (alt_binding,) = widget._bindings.key_to_bindings["alt+e"]
    (ctrl_binding,) = widget._bindings.key_to_bindings["ctrl+e"]

    assert alt_binding.priority is True
    assert ctrl_binding.priority is False
    assert alt_binding.action == ctrl_binding.action == "launch_external_editor"
    assert (
        alt_binding.description == ctrl_binding.description == "Launch External Editor"
    )


def test_explicit_priority_applies_to_every_key() -> None:
    widget = Widget()
    bind(widget, "ctrl+q,f10", "quit", priority=True)

    assert all(
        binding.priority is True
        for key in ("ctrl+q", "f10")
        for binding in widget._bindings.key_to_bindings[key]
    )


def test_key_display_and_show_survive_the_split() -> None:
    widget = Widget()
    bind(
        widget,
        "alt+e,ctrl+e",
        "launch_external_editor",
        description="Launch External Editor",
        key_display="⌥e or ^e",
    )

    for key in ("alt+e", "ctrl+e"):
        (binding,) = widget._bindings.key_to_bindings[key]
        assert binding.key_display == "⌥e or ^e"
        assert binding.show is True
