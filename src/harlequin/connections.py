"""The open database connections, and the profile a connection is saved as.

At most `MAX_OPEN_CONNECTIONS` stay open. Closing one calls nothing itself:
the app closes the database, then drops the slot, so a slot that is gone
cannot still be queried. The last open connection stays until the app quits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from harlequin.adapter import HarlequinAdapter, HarlequinConnection
from harlequin.config import ConfigFile
from harlequin.redact import redact_conn_str

MAX_OPEN_CONNECTIONS = 8
"""How many databases one session will keep open. The next open is refused."""

_PROFILE_NAME = re.compile(r"[A-Za-z0-9_-]+")


@dataclass
class OpenConnection:
    """One database this session has connected to and not yet closed."""

    label: str
    adapter_name: str | None
    adapter: HarlequinAdapter
    connection: HarlequinConnection
    connection_hash: str | None
    conn_str: tuple[str, ...]


class ConnectionShelf:
    """The connections that are open, and which one queries run on."""

    def __init__(self) -> None:
        self._open: list[OpenConnection] = []
        self.active = 0

    def __len__(self) -> int:
        return len(self._open)

    @property
    def slots(self) -> tuple[OpenConnection, ...]:
        return tuple(self._open)

    def current(self) -> OpenConnection | None:
        if not self._open:
            return None
        return self._open[self.active]

    def add(self, slot: OpenConnection) -> bool:
        """Keep `slot` and make it the active one. False when the shelf is full."""
        if len(self._open) >= MAX_OPEN_CONNECTIONS:
            return False
        self._open.append(slot)
        self.active = len(self._open) - 1
        return True

    def select(self, index: int) -> OpenConnection | None:
        if not 0 <= index < len(self._open):
            return None
        self.active = index
        return self._open[index]

    def close_at(self, index: int) -> OpenConnection | None:
        """Remove one slot. None when it is the last one, or the index is wrong.

        The caller closes the database. The active index still names a slot
        that is open.
        """
        if len(self._open) <= 1 or not 0 <= index < len(self._open):
            return None
        slot = self._open.pop(index)
        if index < self.active:
            self.active -= 1
        elif index == self.active:
            self.active = min(self.active, len(self._open) - 1)
        return slot

    def close_all(self) -> list[OpenConnection]:
        """Every slot, and the shelf is empty. The caller closes each one."""
        slots = list(self._open)
        self._open.clear()
        self.active = 0
        return slots


def connection_label(adapter_name: str | None, conn_str: Sequence[str]) -> str:
    """A short name for the connection list. Credentials in the string are masked."""
    shown = redact_conn_str(conn_str)
    target = " ".join(shown) if shown else "(default)"
    label = f"{adapter_name or 'database'} {target}"
    if len(label) <= 80:
        return label
    return label[:77] + "..."


def canonical_adapter_name(requested: str, installed: Sequence[str]) -> str | None:
    """The installed spelling of `requested`, or None when nothing matches."""
    folded = {name.casefold(): name for name in installed}
    return folded.get(requested.strip().casefold())


def profile_name_error(name: str) -> str | None:
    """Why `name` cannot be a profile, or None when it can."""
    if not name:
        return "Enter a name for this connection."
    if name == "None" or _PROFILE_NAME.fullmatch(name) is None:
        return "Use letters, numbers, underscores, and hyphens."
    return None


def save_connection_profile(
    path: Path,
    name: str,
    *,
    adapter_name: str | None,
    conn_str: Sequence[str],
    keymap_names: Sequence[str],
) -> None:
    """Write one profile into `path`, leaving the other profiles in that file.

    `keymap_names` is the plugin list that profile loads, so the keymaps chosen
    for this session are the ones that profile starts with.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("")
    config_file = ConfigFile(path)
    raw_profiles = config_file.relevant_config.get("profiles", {})
    profiles = dict(raw_profiles) if isinstance(raw_profiles, dict) else {}
    existing = profiles.get(name)
    profile: dict[str, object] = dict(existing) if isinstance(existing, dict) else {}
    profile["conn_str"] = list(conn_str)
    profile["keymap_name"] = list(keymap_names)
    if adapter_name:
        profile["adapter"] = adapter_name
    profiles[name] = profile
    config_file.update({"profiles": profiles})
    config_file.write()
