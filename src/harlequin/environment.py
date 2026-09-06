"""Facts about the running installation, for the diagnostics that report them.

`hsql --info` answers "what is installed and what can it do"; a crash report
answers "what was this running on". They are the same questions, so they read
the same facts from here rather than each building their own -- and a fact
worth adding gets added once.

Headless, and it has to stay that way: `hsql` imports it, and a crash report is
built after the app is gone. `adapter_facts()` defers `harlequin.plugins` into
its body for the same reason `--info` does -- importing every installed adapter
is the most expensive thing this module can do, and most callers want none of
it.
"""

from __future__ import annotations

import locale
import os
import platform
import shutil
import sys
from importlib.metadata import PackageNotFoundError, distribution, version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinAdapter

UNKNOWN = "unknown"
"""What an adapter that would not import declares, in place of a capability map."""


def harlequin_version() -> str:
    return version("harlequin")


def python_facts() -> dict[str, str]:
    return {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
    }


def platform_facts() -> dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
    }


def terminal_facts() -> dict[str, Any]:
    """What the TUI is drawing into, which is half of every rendering bug.

    `ssh` is a bool and never the value of `SSH_CONNECTION`, which holds IP
    addresses: whether the session is remote explains a bug, and where it came
    from does not.
    """
    size = shutil.get_terminal_size()
    return {
        "term": os.environ.get("TERM"),
        "term_program": os.environ.get("TERM_PROGRAM"),
        "term_program_version": os.environ.get("TERM_PROGRAM_VERSION"),
        "colorterm": os.environ.get("COLORTERM"),
        "shell": os.environ.get("SHELL") or os.environ.get("COMSPEC"),
        "wsl_distro": os.environ.get("WSL_DISTRO_NAME"),
        "ssh": bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY")),
        "locale": _locale(),
        "size": f"{size.columns}x{size.lines}",
    }


def install_facts() -> dict[str, Any]:
    """How harlequin got onto this machine, which the bug template used to ask.

    pip, uv and pipx all write the `INSTALLER` file; a build that has none is
    reported as null rather than guessed at.
    """
    try:
        installer = distribution("harlequin").read_text("INSTALLER")
    except (PackageNotFoundError, OSError):
        installer = None
    return {
        "installer": installer.strip() if installer else None,
        "in_venv": sys.prefix != sys.base_prefix,
    }


def adapter_facts(only: str | None = None) -> dict[str, dict[str, Any]]:
    """Every installed adapter's declarations, or one's, keyed by adapter name.

    Keyed rather than a list, because the question a caller has is about a name
    they already hold: what can `duckdb` do. An adapter that will not import is
    reported with its capabilities `"unknown"` and the import error beside
    them. Never `false`: guessing false about what an adapter implements is the
    direction that gets someone hurt.
    """
    from harlequin.exception import HarlequinConfigError
    from harlequin.plugins import (
        adapter_distributions,
        adapter_names,
        adapter_versions,
        load_adapter,
    )

    distributions = adapter_distributions()
    versions = adapter_versions()
    names = [only] if only is not None else adapter_names()
    adapters: dict[str, dict[str, Any]] = {}
    for name in names:
        capabilities: dict[str, bool] | str = UNKNOWN
        error: str | None = None
        try:
            adapter_cls = load_adapter(name)
        except HarlequinConfigError as e:
            error = e.msg
        else:
            capabilities = adapter_capabilities(adapter_cls)
        adapters[name] = {
            "distribution": distributions.get(name),
            "version": versions.get(name),
            "capabilities": capabilities,
            "error": error,
        }
    return adapters


def adapter_capabilities(adapter_cls: type[HarlequinAdapter]) -> dict[str, bool]:
    """What one adapter declares, read off the class and never called.

    The names come from the contract rather than a list kept here, so a
    capability added to `HarlequinAdapter` is reported as soon as it exists.
    """
    from harlequin.adapter import HarlequinAdapter

    return {
        name.lower(): bool(getattr(adapter_cls, name, False))
        for name in sorted(vars(HarlequinAdapter))
        if name.startswith("IMPLEMENTS_")
    }


def runtime_report() -> dict[str, Any]:
    """Everything cheap: no adapter is imported and no subprocess is run.

    That is what makes it safe to call from a crash handler, where an import of
    every installed adapter is both slow and a fresh place to crash.
    """
    return {
        "version": harlequin_version(),
        "python": python_facts(),
        "platform": platform_facts(),
        "terminal": terminal_facts(),
        "install": install_facts(),
    }


def _locale() -> str | None:
    try:
        language, encoding = locale.getlocale()
    except ValueError:
        return None
    if language is None and encoding is None:
        return None
    return ".".join(part for part in (language, encoding) if part)
