"""Run-time guards for the import hygiene the headless CLI depends on.

The import-linter contracts in `pyproject.toml` read the static graph, so they
cannot tell a module-scope import from one deferred into the function that needs
it. These tests run the real thing in a subprocess and look at `sys.modules`,
which is the only way to prove a deferral actually defers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
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


def test_building_the_ide_command_imports_only_the_named_adapter(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """The IDE's half of the same two-phase parse.

    An invocation opens the IDE with exactly one adapter, so building its
    command imports exactly one. Every other installed adapter used to be
    `ep.load()`ed on the way to a connection that would never use it, which is
    ~200ms with four of them and grows with the fifth.
    """
    proc = run_python(
        "import sys\n"
        "from harlequin.cli import build_cli\n"
        "build_cli(['-a', 'sqlite', ':memory:'])\n"
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules "
        "if m.startswith('harlequin_')})))\n"
    )
    assert proc.stdout.strip() == "harlequin_sqlite"


def test_the_ides_help_still_documents_every_adapter(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """The deliberate exception, and the one path that still pays for all of them.

    `harlequin --help` lists what every installed adapter takes, because an
    option a reader cannot discover is one they cannot use. The trade is the
    right way round: help is not the invocation anyone waits on.
    """
    proc = run_python(
        "import sys\n"
        "from harlequin.cli import build_cli\n"
        "build_cli(['--help'])\n"
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules "
        "if m.startswith('harlequin_')})))\n"
    )
    # a superset: whatever else the machine running this has installed
    assert {"harlequin_duckdb", "harlequin_sqlite"} <= set(
        proc.stdout.strip().split(",")
    )


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


@pytest.mark.parametrize(
    "mode,forbidden",
    [
        # a document, so it needs neither the row machinery nor a database.
        # It does write TOML, so tomlkit is not on its list
        ("show", ("duckdb", "pyarrow")),
        # rows, so it pays for pyarrow -- and for no database driver, because
        # `ResultSet.text_columns()` returns strings unchanged, and no tomlkit,
        # because a listing is not a TOML document
        ("list-profiles", ("duckdb", "tomlkit")),
    ],
)
def test_the_config_modes_import_no_database(
    mode: str,
    forbidden: tuple[str, ...],
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """A mode that reports on config files must work when the database does not.

    So it imports no adapter, and nothing an adapter would have brought with
    it. `--format csv|json|parquet` is the exception a caller asks for by name:
    those are written by `harlequin.export`, which serializes through duckdb or
    pyarrow whatever produced the rows.
    """
    proc = run_python(
        "import sys\n"
        f"sys.argv = ['hsql', '--config', {mode!r}]\n"
        "from harlequin.hsql import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        f"print(','.join(m for m in {forbidden!r} if m in sys.modules), "
        "file=sys.stderr)\n"
        "print(','.join(m for m in sys.modules if m.startswith('harlequin_')), "
        "file=sys.stderr)\n"
    )
    leaked = [m for m in proc.stderr.strip().replace("\n", ",").split(",") if m]
    assert not leaked, f"`hsql --config {mode}` imported {leaked}"


def test_config_validate_imports_only_the_adapters_its_profiles_name(
    tmp_path: Path, run_python: Callable[[str], subprocess.CompletedProcess[str]]
) -> None:
    """The one mode here that pays for adapters, paying for as few as it can.

    Checking a profile's options against the ones its adapter declares takes
    importing that adapter -- and only that one. A mode that imported every
    installed adapter to check a config naming one would cost a caller with
    four of them four times what the answer is worth. It is rows rather than a
    document, so it pays for pyarrow and not for tomlkit, like `list-profiles`.

    `tmp_path` is the subprocess's cwd and its home, so this file is the whole
    of the config it discovers.
    """
    (tmp_path / ".harlequin.toml").write_text(
        '[profiles.lite]\nadapter = "sqlite"\nread_only = true\n'
    )
    proc = run_python(
        "import sys\n"
        "sys.argv = ['hsql', '--config', 'validate']\n"
        "from harlequin.hsql import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules "
        "if m.startswith('harlequin_')})), file=sys.stderr)\n"
        "print(','.join(m for m in ('duckdb', 'tomlkit') if m in sys.modules), "
        "file=sys.stderr)\n"
    )
    # two lines, and the second is empty when nothing forbidden was imported
    adapters, forbidden = proc.stderr.split("\n")[:2]
    assert adapters == "harlequin_sqlite"
    assert not forbidden


def test_config_schema_writes_a_document_and_reads_no_file(
    tmp_path: Path, run_python: Callable[[str], subprocess.CompletedProcess[str]]
) -> None:
    """The second mode that pays for adapters on purpose, and pays for nothing else.

    Describing the options an adapter declares means importing every installed
    adapter, as `--spec` does. It is a document rather than rows, so it never
    reaches the execution core, and it reads no config file, so no tomlkit -- a
    schema says what a config file may hold whether or not this machine has
    one.

    `tmp_path` is the subprocess's cwd and its home, so this file is the whole
    of the config it would discover.
    """
    (tmp_path / ".harlequin.toml").write_text('[profiles.lite]\nadapter = "sqlite"\n')
    proc = run_python(
        "import sys\n"
        "sys.argv = ['hsql', '--config', 'schema']\n"
        "from harlequin.hsql import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules "
        "if m.startswith('harlequin_')})), file=sys.stderr)\n"
        "print(','.join(m for m in ('harlequin.query', 'tomlkit') "
        "if m in sys.modules), file=sys.stderr)\n"
    )
    adapters, forbidden = proc.stderr.split("\n")[:2]
    # both of the bundled ones, where every other mode here costs at most one
    assert {"harlequin_duckdb", "harlequin_sqlite"} <= set(adapters.split(","))
    assert not forbidden


def test_config_init_imports_tomlkit_and_one_adapter(
    tmp_path: Path, run_python: Callable[[str], subprocess.CompletedProcess[str]]
) -> None:
    """The one mode that writes a file, and so the one that pays for tomlkit.

    It pays for one adapter too, because the options it writes into a profile
    are the ones that adapter declares -- and for nothing else: it writes a
    file rather than rows, and connects to nothing.

    `tmp_path` is the subprocess's cwd and its home, so the file it writes is
    the only config there is.
    """
    proc = run_python(
        "import sys\n"
        "sys.argv = ['hsql', '--config', 'init', '-P', 'lite', '-a', 'sqlite']\n"
        "from harlequin.hsql import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules "
        "if m.startswith('harlequin_')})), file=sys.stderr)\n"
        "print(','.join(m for m in ('duckdb', 'pyarrow') "
        "if m in sys.modules), file=sys.stderr)\n"
        "print('tomlkit' in sys.modules, file=sys.stderr)\n"
    )
    # the last three lines, because this mode says what it wrote on stderr too
    adapters, forbidden, tomlkit = proc.stderr.strip().split("\n")[-3:]
    assert adapters == "harlequin_sqlite"
    assert not forbidden
    # the other half of the deferral: the mode that needs it still gets it
    assert tomlkit == "True"
    assert (tmp_path / ".harlequin.toml").exists()


def test_spec_imports_only_the_adapter_it_was_narrowed_to(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """`--spec` is the one mode that pays for adapters on purpose.

    Reading an adapter's options means importing it, so the whole document
    costs every installed adapter -- which is fine for a once-per-task lookup
    and the reason `-a` exists here. Under `-a` it costs one, and it still
    writes a document rather than rows, so no pyarrow and no tomlkit.
    """
    proc = run_python(
        "import sys\n"
        "sys.argv = ['hsql', '--spec', '-a', 'sqlite']\n"
        "from harlequin.hsql import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules "
        "if m.startswith('harlequin_')})), file=sys.stderr)\n"
        "print(','.join(m for m in ('duckdb', 'pyarrow', 'tomlkit') "
        "if m in sys.modules), file=sys.stderr)\n"
    )
    adapters, forbidden = proc.stderr.split("\n")[:2]
    assert adapters == "harlequin_sqlite"
    assert not forbidden


def test_info_imports_only_the_adapter_it_was_narrowed_to(
    run_python: Callable[[str], subprocess.CompletedProcess[str]],
) -> None:
    """`--info` reads capabilities off the adapter class, so it imports one per
    adapter it reports -- and under `-a`, exactly one.

    It opens no connection, and it writes a document rather than rows, so no
    pyarrow and no tomlkit either.
    """
    proc = run_python(
        "import sys\n"
        "sys.argv = ['hsql', '--info', '-a', 'sqlite']\n"
        "from harlequin.hsql import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules "
        "if m.startswith('harlequin_')})), file=sys.stderr)\n"
        "print(','.join(m for m in ('duckdb', 'pyarrow', 'tomlkit') "
        "if m in sys.modules), file=sys.stderr)\n"
    )
    adapters, forbidden = proc.stderr.split("\n")[:2]
    assert adapters == "harlequin_sqlite"
    assert not forbidden


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
