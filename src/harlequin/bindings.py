from __future__ import annotations

from typing import TYPE_CHECKING

from harlequin.exception import HarlequinBindingError

if TYPE_CHECKING:
    from textual.app import App
    from textual.widget import Widget


def bind(
    target: Widget | App,
    keys: str,
    action: str,
    description: str | None = None,
    show: bool = True,
    key_display: str | None = None,
    priority: bool = False,
) -> None:
    try:
        target._bindings.bind(
            keys=keys,
            action=action,
            description=(
                description
                if description
                else " ".join([w.capitalize() for w in action.split("_")])
            ),
            show=show or bool(key_display),
            key_display=key_display,
            priority=priority,
        )
    except Exception as e:
        raise HarlequinBindingError(
            title="Error configuring key bindings",
            msg=(
                f"{e}\nContext: {keys=}, {action=}, {description=}, {show=}, "
                f"{key_display=}, {priority=}."
            ),
        ) from e
