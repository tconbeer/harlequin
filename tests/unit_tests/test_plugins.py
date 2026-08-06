from __future__ import annotations

import subprocess
from typing import Callable

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


def test_plugin_load_failure_goes_to_stderr(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """A plug-in that won't import must not contaminate piped output."""
    proc = run_python(
        "from unittest.mock import MagicMock, patch\n"
        "ep = MagicMock()\n"
        "ep.name = 'broken'\n"
        "ep.load.side_effect = ImportError('no module named nope', name='nope')\n"
        "with patch('harlequin.plugins.entry_points', return_value=[ep]):\n"
        "    from harlequin.plugins import load_adapter_plugins\n"
        "    load_adapter_plugins()\n"
    )
    assert proc.stdout == ""
    assert "could not load the installed plug-in" in proc.stderr
