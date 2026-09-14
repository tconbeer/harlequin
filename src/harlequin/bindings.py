from __future__ import annotations

from typing import TYPE_CHECKING

from harlequin.exception import HarlequinBindingError

if TYPE_CHECKING:
    from textual.app import App
    from textual.widget import Widget


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
    except Exception as e:
        raise HarlequinBindingError(
            title="Error configuring key bindings",
            msg=(
                f"{e}\nContext: {keys=}, {action=}, {description=}, {show=}, "
                f"{key_display=}, {priority=}."
            ),
        ) from e
