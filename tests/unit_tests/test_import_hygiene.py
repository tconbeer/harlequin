"""Run-time guards for the import hygiene the headless CLI depends on.

The import-linter contracts in `pyproject.toml` read the static graph, so they
cannot tell a module-scope import from one deferred into the function that needs
it. These tests run the real thing in a subprocess and look at `sys.modules`,
which is the only way to prove a deferral actually defers.
"""

from __future__ import annotations

import subprocess
from typing import Callable

import pytest

# Importing any of these must not drag in the TUI. They are the modules an
# adapter -- or a headless front end -- reaches for.
HEADLESS_IMPORTS = [
    "import harlequin",
    "import harlequin.adapter",
    "import harlequin.autocomplete",
    "import harlequin.catalog",
    "import harlequin.config",
    "import harlequin.exception",
    "import harlequin.export",
    "import harlequin.hsql",
    "import harlequin.hsql.cli",
    "import harlequin.keymap",
    "import harlequin.layout",
    "import harlequin.options",
    "import harlequin.plugins",
    "import harlequin.query",
    "import harlequin.statements",
    "import harlequin.transaction_mode",
    "import harlequin_duckdb",
    "import harlequin_sqlite",
    "from textual_fastdatatable.backend import create_backend",
]

FORBIDDEN = ("textual", "questionary", "prompt_toolkit", "sqlfmt")


@pytest.mark.parametrize("statement", HEADLESS_IMPORTS)
def test_headless_imports_do_not_load_the_tui(
    statement: str, run_python: Callable[[str], subprocess.CompletedProcess[str]]
) -> None:
    proc = run_python(
        f"{statement}\n"
        "import sys\n"
        f"print(','.join(m for m in {FORBIDDEN!r} if m in sys.modules))\n"
    )
    leaked = [m for m in proc.stdout.strip().split(",") if m]
    assert not leaked, f"{statement!r} imported {leaked}"


def test_building_the_hsql_command_imports_no_adapter(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """The point of hsql's two-phase parse, and it regresses quietly.

    `hsql --help` names no adapter, so it renders the adapter-agnostic surface
    and must not pay `ep.load()` for every adapter installed to do it.
    """
    proc = run_python(
        "import sys\n"
        "from harlequin.hsql.cli import build_cli\n"
        "build_cli(['--help'])\n"
        "print(','.join(m for m in sys.modules if m.startswith('harlequin_')))\n"
    )
    loaded = [m for m in proc.stdout.strip().split(",") if m]
    assert not loaded, f"building `hsql --help` imported {loaded}"


def test_public_names_still_resolve() -> None:
    """Every name `harlequin/__init__.py` exported before it went lazy.

    Each must resolve to the same object as a direct import from its home
    module, and resolve to the *same* object twice (the second lookup is served
    from the module globals, not `__getattr__`).
    """
    import harlequin
    from harlequin.adapter import (
        HarlequinAdapter,
        HarlequinConnection,
        HarlequinCursor,
    )
    from harlequin.app import Harlequin
    from harlequin.autocomplete import HarlequinCompletion
    from harlequin.keymap import HarlequinKeyBinding, HarlequinKeyMap
    from harlequin.keys_app import HarlequinKeys
    from harlequin.options import HarlequinAdapterOption, HarlequinCopyFormat
    from harlequin.transaction_mode import HarlequinTransactionMode

    expected = {
        "Harlequin": Harlequin,
        "HarlequinAdapter": HarlequinAdapter,
        "HarlequinAdapterOption": HarlequinAdapterOption,
        "HarlequinCompletion": HarlequinCompletion,
        "HarlequinConnection": HarlequinConnection,
        "HarlequinCopyFormat": HarlequinCopyFormat,
        "HarlequinCursor": HarlequinCursor,
        "HarlequinTransactionMode": HarlequinTransactionMode,
        "HarlequinKeys": HarlequinKeys,
        "HarlequinKeyMap": HarlequinKeyMap,
        "HarlequinKeyBinding": HarlequinKeyBinding,
    }
    assert sorted(expected) == sorted(harlequin.__all__)
    for name, obj in expected.items():
        assert getattr(harlequin, name) is obj
        assert getattr(harlequin, name) is getattr(harlequin, name)

    assert sorted(dir(harlequin)) == sorted(harlequin.__all__)


def test_unknown_attribute_still_raises_attribute_error() -> None:
    import harlequin

    with pytest.raises(AttributeError):
        _ = harlequin.NotAThing
