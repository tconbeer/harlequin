"""The connection shelf and the profile it can be saved as."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from harlequin.adapter import HarlequinAdapter, HarlequinConnection
from harlequin.connections import (
    MAX_OPEN_CONNECTIONS,
    ConnectionShelf,
    OpenConnection,
    canonical_adapter_name,
    profile_name_error,
    save_connection_profile,
)


def _slot(name: str) -> OpenConnection:
    return OpenConnection(
        label=name,
        adapter_name="duckdb",
        adapter=cast(HarlequinAdapter, object()),
        connection=cast(HarlequinConnection, object()),
        connection_hash=name,
        conn_str=(name,),
    )


def test_the_shelf_refuses_a_ninth_connection() -> None:
    shelf = ConnectionShelf()
    for index in range(MAX_OPEN_CONNECTIONS):
        assert shelf.add(_slot(str(index))) is True
    assert shelf.add(_slot("extra")) is False
    assert len(shelf) == MAX_OPEN_CONNECTIONS
    current = shelf.current()
    assert current is not None
    assert current.label == str(MAX_OPEN_CONNECTIONS - 1)


def test_the_last_connection_cannot_be_closed() -> None:
    shelf = ConnectionShelf()
    shelf.add(_slot("only"))
    assert shelf.close_at(0) is None
    assert len(shelf) == 1
    current = shelf.current()
    assert current is not None
    assert current.label == "only"


def test_closing_a_slot_keeps_the_active_index_on_an_open_one() -> None:
    shelf = ConnectionShelf()
    for name in ("a", "b", "c"):
        shelf.add(_slot(name))
    assert shelf.active == 2
    closed = shelf.close_at(0)
    assert closed is not None and closed.label == "a"
    assert shelf.active == 1
    current = shelf.current()
    assert current is not None and current.label == "c"
    closed = shelf.close_at(1)
    assert closed is not None and closed.label == "c"
    assert shelf.active == 0
    current = shelf.current()
    assert current is not None and current.label == "b"


def test_closing_before_the_active_slot_does_not_move_it() -> None:
    shelf = ConnectionShelf()
    for name in ("a", "b", "c"):
        shelf.add(_slot(name))
    shelf.select(0)
    closed = shelf.close_at(1)
    assert closed is not None and closed.label == "b"
    assert shelf.active == 0
    assert [slot.label for slot in shelf.slots] == ["a", "c"]


def test_close_all_empties_the_shelf() -> None:
    shelf = ConnectionShelf()
    shelf.add(_slot("a"))
    shelf.add(_slot("b"))
    closed = shelf.close_all()
    assert [slot.label for slot in closed] == ["a", "b"]
    assert len(shelf) == 0
    assert shelf.current() is None


def test_a_profile_name_rejects_none_and_punctuation() -> None:
    assert profile_name_error("") == "Enter a name for this connection."
    assert profile_name_error("None") is not None
    assert profile_name_error("my profile") is not None
    assert profile_name_error("warehouse-1") is None


def test_an_adapter_name_matches_without_regard_to_case() -> None:
    assert canonical_adapter_name(" DuckDB ", ["sqlite", "duckdb"]) == "duckdb"
    assert canonical_adapter_name("missing", ["duckdb"]) is None


def test_saving_a_profile_leaves_the_others(tmp_path: Path) -> None:
    path = tmp_path / "harlequin" / "config.toml"
    path.parent.mkdir()
    path.write_text(
        "\n".join(
            [
                "[profiles.kept]",
                'adapter = "sqlite"',
                'conn_str = ["kept.db"]',
                'theme = "harlequin"',
                "",
                "[profiles.demo]",
                'theme = "monokai"',
                "",
            ]
        )
    )
    save_connection_profile(
        path,
        "demo",
        adapter_name="duckdb",
        conn_str=("warehouse.db",),
        keymap_names=("vscode", "files"),
    )
    saved: dict[str, Any] = tomllib.loads(path.read_text()).get("profiles", {})
    assert saved["kept"]["adapter"] == "sqlite"
    assert saved["kept"]["conn_str"] == ["kept.db"]
    assert saved["kept"]["theme"] == "harlequin"
    assert saved["demo"]["adapter"] == "duckdb"
    assert saved["demo"]["conn_str"] == ["warehouse.db"]
    assert saved["demo"]["keymap_name"] == ["vscode", "files"]
    assert saved["demo"]["theme"] == "monokai"
