import pytest

from harlequin.cli import DEFAULT_KEYMAP_NAMES
from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyMap
from harlequin.plugins import load_keymap_plugins


def test_load_keymap_plugins() -> None:
    built_in_keymaps = load_keymap_plugins(user_defined_keymaps=[])
    assert len(built_in_keymaps) == 1
    assert DEFAULT_KEYMAP_NAMES[0] in built_in_keymaps
    assert isinstance(built_in_keymaps[DEFAULT_KEYMAP_NAMES[0]], HarlequinKeyMap)
    assert built_in_keymaps[DEFAULT_KEYMAP_NAMES[0]].bindings


def test_do_not_load_keymaps_that_duplicate_plugin_names() -> None:
    my_map = HarlequinKeyMap(name=DEFAULT_KEYMAP_NAMES[0], bindings=[])
    with pytest.raises(HarlequinConfigError) as exc_info:
        _ = load_keymap_plugins(user_defined_keymaps=[my_map])
    assert "vscode" in str(exc_info)
