from __future__ import annotations

import subprocess
from typing import Callable
from unittest.mock import MagicMock

import pytest

from harlequin.cli import DEFAULT_KEYMAP_NAMES
from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyMap
from harlequin.plugins import (
    adapter_names,
    load_adapter,
    load_adapter_plugins,
    load_keymap_plugins,
)


def _adapter_packages(code: str) -> str:
    """A snippet that runs `code`, then reports the adapters it imported."""
    return (
        f"{code}\n"
        "import sys\n"
        "print(','.join(sorted({m.split('.')[0] for m in list(sys.modules)"
        " if m.startswith('harlequin_')})))\n"
    )


def test_adapter_names_agree_with_the_loaded_adapters() -> None:
    """The cheap listing and the expensive one must not disagree.

    `adapter_names()` exists to avoid importing every adapter just to find out
    what is installed, so it has to name the same ones. Not equality: an adapter
    that is installed but won't import is named here and dropped by
    `load_adapter_plugins()`, which is the difference between the two functions
    and not a disagreement about what is installed.
    """
    names = adapter_names()
    assert set(load_adapter_plugins().keys()) <= set(names)
    assert "duckdb" in names
    assert "sqlite" in names
    assert names == sorted(names)


def test_adapter_names_imports_no_adapter(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """Naming the installed adapters must not import any of them."""
    proc = run_python(
        _adapter_packages(
            "from harlequin.plugins import adapter_names\n"
            "assert 'duckdb' in adapter_names()"
        )
    )
    assert proc.stdout.strip() == ""


def test_load_adapter_loads_exactly_one_adapter(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    assert load_adapter("duckdb") is load_adapter_plugins()["duckdb"]
    proc = run_python(
        _adapter_packages(
            "from harlequin.plugins import load_adapter\nload_adapter('duckdb')"
        )
    )
    assert proc.stdout.strip() == "harlequin_duckdb"


def test_load_adapter_raises_for_an_unknown_name() -> None:
    with pytest.raises(HarlequinConfigError) as exc_info:
        load_adapter("not-an-adapter")
    # names the adapter that was asked for, and the ones that could have been
    assert "not-an-adapter" in str(exc_info.value)
    assert "duckdb" in str(exc_info.value)


def test_load_adapter_raises_when_the_adapter_will_not_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named adapter that won't import is fatal, unlike one found by scanning.

    `load_adapter_plugins()` warns and carries on, because the app can still run
    with the adapters that did load. Here the caller asked for this one by name,
    so there is nothing to fall back to.
    """
    ep = MagicMock()
    ep.name = "broken"
    ep.load.side_effect = ImportError("no module named nope", name="nope")
    monkeypatch.setattr("harlequin.plugins.entry_points", lambda group: [ep])
    with pytest.raises(HarlequinConfigError) as exc_info:
        load_adapter("broken")
    assert "broken" in str(exc_info.value)
    assert "no module named nope" in str(exc_info.value)


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
