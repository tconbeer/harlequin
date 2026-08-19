"""`--info`: what is installed, what it can do, and what config it is reading.

The diagnostic an agent runs before it runs anything else, and the one a human
asks for when a run did something they did not expect. It answers four
questions in one document: which versions of what are installed, which config
files this machine has and in what order they are read, which profile an
invocation would run under, and what each installed adapter *declares* it can
do.

**It opens no connection**, which is the point rather than an omission: the
diagnostic a caller reaches for when the database is unreachable must not
itself require the database. Capabilities are class attributes for exactly that
reason -- reading one costs an import of the adapter class and never a
connection.

**It does import**, though, and that is the cost this mode pays: every installed
adapter, so that every capability is read from the adapter itself rather than
guessed. `-a NAME` narrows the document to one adapter and the import to one
with it, which is the common case of asking about the adapter you are using.

An adapter that will not import is reported with its capabilities `"unknown"`
and the import error beside them. Never `false`: guessing false about what an
adapter implements is the direction that gets someone hurt.
"""

from __future__ import annotations

import json
import platform
import sys
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, BinaryIO

from harlequin.config import DEFAULT_ADAPTER, discover_config_files, resolve_profile
from harlequin.exception import HarlequinConfigError
from harlequin.hsql import diagnostics
from harlequin.hsql.diagnostics import ExitCode

if TYPE_CHECKING:
    from pathlib import Path

    from harlequin.adapter import HarlequinAdapter

JSON = "json"
"""The one `--format` a document mode answers to. `none` writes nothing."""

NONE = "none"

UNKNOWN = "unknown"
"""What an adapter that would not import declares, in place of a capability map."""


def report(
    out: BinaryIO,
    *,
    adapter: str | None,
    profile_name: str | None,
    config_path: Path | None,
    format_name: str,
    format_chosen: bool,
) -> ExitCode:
    """Write the report, and return the code it exits with.

    Exit 0 whatever it found. Every problem this mode can run into -- a config
    file it cannot read, a profile that is not defined, an adapter that will not
    import -- is a fact about the installation and so part of the answer, rather
    than a reason to refuse one.
    """
    if format_name == NONE:
        return ExitCode.OK
    if format_name != JSON and format_chosen:
        diagnostics.report_document_format_ignored("--info", format_name)

    profile = _profile(profile_name, config_path)
    document = {
        "program": "hsql",
        "version": version("harlequin"),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "config": _config_files(config_path),
        "profile": profile,
        "adapter": _adapter_in_use(adapter, profile["options"]),
        "adapters": _adapters(adapter),
    }
    out.write((json.dumps(document, indent=2, default=str) + "\n").encode("utf-8"))
    return ExitCode.OK


def _config_files(config_path: Path | None) -> dict[str, Any]:
    """Every config file this machine has, highest priority first.

    Discovery rather than the merge: a file that defines nothing hsql reads is
    still a file a caller put there and expects to be read, and the order is
    what answers "which one is winning". `--config show` is the mode that says
    which value came from where.

    Raises: HarlequinConfigError if `--config-path` names a file that is not
    there, which is a usage error rather than a fact about the installation.
    """
    return {
        "path": _text(config_path),
        "files": [str(path) for path in discover_config_files(config_path)],
    }


def _profile(profile_name: str | None, config_path: Path | None) -> dict[str, Any]:
    """The profile an invocation would run under, whole.

    Resolved the way a run resolves it, stopping at the file that defines it, so
    that this reports the profile `hsql -c ...` would use rather than one merged
    from files that run would never open. `--config validate` is the mode that
    reads all of them.

    `name` is null when nothing names one, and `options` is null when the name
    resolved to no profile -- a `-P` typo, or a `default_profile` naming
    nothing, both of which a run refuses over and this reports.
    """
    try:
        name, options = resolve_profile(config_path, profile_name)
    except (HarlequinConfigError, OSError) as e:
        # a config file this mode could not read, or a name nothing defines, is
        # the answer rather than a refusal: a caller whose config is broken is
        # one of the callers most likely to be reading this
        message = e.msg if isinstance(e, HarlequinConfigError) else str(e)
        return {"name": profile_name, "options": None, "error": message}
    return {"name": name, "options": options, "error": None}


def _adapter_in_use(typed: str | None, options: Any) -> dict[str, Any]:
    """Which adapter an invocation would connect with, and what decided it.

    Three things can, in this order, and knowing which one did is the whole
    answer to "why is it connecting to that": `-a`, the profile, or the default
    nothing named.
    """
    if typed is not None:
        return {"name": typed, "from": "-a"}
    named = (options or {}).get("adapter")
    if named:
        return {"name": str(named), "from": "profile"}
    return {"name": DEFAULT_ADAPTER, "from": "default"}


def _adapters(only: str | None) -> dict[str, Any]:
    """Every installed adapter's declarations, or one's, keyed by adapter name.

    Keyed rather than a list, because the question a caller has is about a name
    they already hold: what can `duckdb` do.
    """
    from harlequin.plugins import (
        adapter_distributions,
        adapter_names,
        adapter_versions,
        load_adapter,
    )

    distributions = adapter_distributions()
    versions = adapter_versions()
    names = [only] if only is not None else adapter_names()
    adapters: dict[str, Any] = {}
    for name in names:
        capabilities: dict[str, bool] | str = UNKNOWN
        error: str | None = None
        try:
            adapter_cls = load_adapter(name)
        except HarlequinConfigError as e:
            # both halves are worth reporting: an agent reading the document
            # sees why the capabilities are unknown, and a human sees it on
            # stderr
            error = e.msg
            diagnostics.note(f"{name} is installed, but could not be imported.")
        else:
            capabilities = _capabilities(adapter_cls)
        adapters[name] = {
            "distribution": distributions.get(name),
            "version": versions.get(name),
            "capabilities": capabilities,
            "error": error,
        }
    return adapters


def _capabilities(adapter_cls: type[HarlequinAdapter]) -> dict[str, bool]:
    """What one adapter declares, read off the class and never called.

    The names come from the contract rather than a list kept here, so a
    capability added to `HarlequinAdapter` is reported by this mode as soon as
    it exists.
    """
    from harlequin.adapter import HarlequinAdapter

    return {
        name.lower(): bool(getattr(adapter_cls, name, False))
        for name in sorted(vars(HarlequinAdapter))
        if name.startswith("IMPLEMENTS_")
    }


def _text(value: Any) -> str | None:
    return None if value is None else str(value)
