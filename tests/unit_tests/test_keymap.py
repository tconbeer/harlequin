from __future__ import annotations

import pickle

import pytest

from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyBinding, HarlequinKeyMap


def test_keybinding_to_dict_does_not_mutate_instance() -> None:
    binding = HarlequinKeyBinding(keys="ctrl+q", action="quit")
    assert binding.key_display is None
    assert hasattr(binding, "key_display") is True
    assert "key_display" in binding.__dict__

    d = binding.to_dict()
    assert d == {"keys": "ctrl+q", "action": "quit"}
    assert "key_display" not in d

    # Ensure to_dict did not pop key_display from self.__dict__
    assert hasattr(binding, "key_display") is True
    assert binding.key_display is None
    assert "key_display" in binding.__dict__

    # Calling to_dict repeatedly is idempotent
    d2 = binding.to_dict()
    assert d2 == d

    # Serialization and repr work cleanly
    pickled = pickle.dumps(binding)
    unpickled = pickle.loads(pickled)
    assert unpickled == binding
    assert "key_display=None" in repr(binding)


def test_keybinding_to_dict_with_key_display() -> None:
    binding = HarlequinKeyBinding(
        keys="ctrl+h", action="help", key_display="Help Display"
    )
    d = binding.to_dict()
    assert d == {
        "keys": "ctrl+h",
        "action": "help",
        "key_display": "Help Display",
    }
    assert binding.key_display == "Help Display"


def test_keymap_from_config_typo_fix_key_display() -> None:
    with pytest.raises(HarlequinConfigError) as exc_info:
        HarlequinKeyMap.from_config(
            name="bad_prop_map",
            bindings=[
                {
                    "keys": "ctrl+w",
                    "action": "quit",
                    "extra_property": "bad",  # type: ignore[typeddict-unknown-key]
                }
            ],
        )
    err = exc_info.value
    assert "key_display" in err.msg
    assert "key_profile" not in err.msg


def test_keymap_from_config_and_to_dict_valid() -> None:
    keymap = HarlequinKeyMap.from_config(
        name="valid_map",
        bindings=[
            {"keys": "ctrl+q", "action": "quit"},
            {"keys": "ctrl+h", "action": "help", "key_display": "Help"},
        ],
    )
    assert keymap.name == "valid_map"
    assert len(keymap.bindings) == 2
    raw = keymap.to_dict()
    assert raw == {
        "name": "valid_map",
        "bindings": [
            {"keys": "ctrl+q", "action": "quit"},
            {"keys": "ctrl+h", "action": "help", "key_display": "Help"},
        ],
    }
