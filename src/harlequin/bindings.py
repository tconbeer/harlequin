from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from textual.binding import Binding

from harlequin.exception import HarlequinBindingError

if TYPE_CHECKING:
    from textual.app import App
    from textual.widget import Widget

KEYMAP_BINDING_ID = "harlequin.keymap"
"""Marks a binding `bind` added, so a keymap reload can drop it and no others."""


def key_needs_priority(key: str) -> bool:
    """Must a binding on this key be checked before the focused widget sees it?

    A terminal sends alt+e as ESC e, which Textual parses into the key `alt+e`
    carrying the character "e", so a focused Input or TextArea inserts the
    letter and a non-priority binding on the key never fires. Only a
    single-character key takes the `alt+` prefix, and ctrl replaces the
    character with a control code, so alt+<character> and alt+shift+<character>
    are the modified spellings that arrive as text. An unmodified printable key
    arrives as text too, but it is indistinguishable from typing, so it keeps
    Textual's behavior.
    """
    modifiers, separator, unmodified_key = key.rpartition("+")
    if not separator or len(unmodified_key) != 1:
        return False
    modifier_names = set(modifiers.split("+"))
    return (
        modifier_names in ({"alt"}, {"alt", "shift"}) and unmodified_key.isprintable()
    )


def clear_keymap_bindings(target: Widget | App) -> None:
    """Drop bindings added from a keymap. A class binding, whose id is unset, stays.

    `BindingsMap.bind` appends, so loading a keymap again would stack a second
    copy of every key. Clearing the tagged ones first keeps a reload to one.
    """
    kept: dict[str, list[Binding]] = {}
    for key, bindings in target._bindings.key_to_bindings.items():
        remaining = [binding for binding in bindings if binding.id != KEYMAP_BINDING_ID]
        if remaining:
            kept[key] = remaining
    target._bindings.key_to_bindings = kept


def _tag_last(target: Widget | App, key: str, action: str) -> None:
    """The binding just appended for `key` came from a keymap."""
    found = target._bindings.key_to_bindings.get(key)
    if not found:
        return
    last = found[-1]
    if last.action == action and last.id is None:
        found[-1] = replace(last, id=KEYMAP_BINDING_ID)


def bind(
    target: Widget | App,
    keys: str,
    action: str,
    description: str | None = None,
    show: bool = True,
    key_display: str | None = None,
    priority: bool = False,
) -> None:
    priority_keys: list[str] = []
    bubbling_keys: list[str] = []
    for key in keys.split(","):
        key = key.strip()
        if priority or key_needs_priority(key):
            priority_keys.append(key)
        else:
            bubbling_keys.append(key)

    try:
        for grouped_keys, keys_are_priority in (
            (priority_keys, True),
            (bubbling_keys, False),
        ):
            if not grouped_keys:
                continue
            target._bindings.bind(
                keys=",".join(grouped_keys),
                action=action,
                description=(
                    description
                    if description
                    else " ".join([w.capitalize() for w in action.split("_")])
                ),
                show=show or bool(key_display),
                key_display=key_display,
                priority=keys_are_priority,
            )
            for key in grouped_keys:
                _tag_last(target, key, action)
    except Exception as e:
        raise HarlequinBindingError(
            title="Error configuring key bindings",
            msg=(
                f"{e}\nContext: {keys=}, {action=}, {description=}, {show=}, "
                f"{key_display=}, {priority=}."
            ),
        ) from e
