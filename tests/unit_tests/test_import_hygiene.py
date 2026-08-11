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

FORBIDDEN = ("textual", "questionary", "prompt_toolkit", "sqlfmt", "rich")
"""`rich` is here for the same reason as the rest: nothing headless renders.

It is the one that arrives sideways rather than from an import of ours --
`textual_fastdatatable.backend` pulled it in until 0.17.1 deferred it -- so the
guard is worth more here than the import-linter contracts, which only see this
repo's own graph.
"""


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


@pytest.mark.parametrize("argv", [["--help"], ["--version"]])
def test_building_the_hsql_command_imports_no_adapter(
    argv: list[str], run_python: Callable[[str], subprocess.CompletedProcess[str]]
) -> None:
    """The point of hsql's two-phase parse, and it regresses quietly.

    Neither names an adapter: `--help` renders the adapter-agnostic surface and
    `--version` prints a string that does not depend on one, so neither must pay
    `ep.load()` for every adapter installed to do it.
    """
    proc = run_python(
        "import sys\n"
        "from harlequin.hsql.cli import build_cli\n"
        f"build_cli({argv!r})\n"
        "print(','.join(m for m in sys.modules if m.startswith('harlequin_')))\n"
    )
    loaded = [m for m in proc.stdout.strip().split(",") if m]
    assert not loaded, f"building `hsql {' '.join(argv)}` imported {loaded}"


# Not TUI modules, but slow ones on the path every start-up walks, each kept off
# it by a deferral the static graph cannot see. The cost is the reason: tomlkit
# parses a 10KB pyproject.toml ~30x slower than tomllib, and wcwidth costs
# ~25ms to import for a fast path that returns without calling it.
DEFERRED_ON_STARTUP = ("tomlkit", "wcwidth")


def test_reading_config_does_not_import_tomlkit(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """Reads go through tomllib; tomlkit is for writes, which are rare.

    Start-up reads config -- including whatever `pyproject.toml` is in the
    working directory -- so an eager tomlkit here is paid by every invocation.
    """
    proc = run_python(
        "import sys\n"
        "from harlequin.config import load_config\n"
        "load_config(config_path=None)\n"
        "print('tomlkit' in sys.modules)\n"
    )
    assert proc.stdout.strip() == "False"


def test_an_all_ascii_run_defers_the_slow_startup_imports(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """A full `hsql -c 'select 1'`, end to end, in a clean interpreter."""
    proc = run_python(
        "import sys\n"
        "sys.argv = ['hsql', '-c', 'select 1']\n"
        "from harlequin.hsql import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        f"print(','.join(m for m in {DEFERRED_ON_STARTUP!r} if m in sys.modules), "
        "file=sys.stderr)\n"
    )
    leaked = [m for m in proc.stderr.strip().split(",") if m]
    assert not leaked, f"`hsql -c 'select 1'` imported {leaked}"


def test_non_ascii_output_still_measures_correctly(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """The other half of the wcwidth deferral: it still loads when needed.

    A deferral that never fires on the path that needs it would be a silent
    misalignment bug rather than a slow import, so assert the column is padded
    to the width the ideographs actually occupy.
    """
    proc = run_python(
        "import sys\n"
        "sys.argv = ['hsql', '-c', \"select '\\u4e2d\\u6587' as a, 'ab' as b\"]\n"
        "from harlequin.hsql import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print('wcwidth' in sys.modules, file=sys.stderr)\n"
    )
    assert proc.stderr.strip() == "True"
    # two ideographs are four cells, so column `a` is four wide and its rule is
    # six. Measured with `len()` instead it would be two wide and rule four --
    # which is the misalignment this asserts against, so match the whole line.
    assert "------+----" in proc.stdout


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
