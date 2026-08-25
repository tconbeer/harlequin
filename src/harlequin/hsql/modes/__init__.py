"""One module per `hsql` mode, imported only when that mode is asked for.

A mode is an option rather than a subcommand (`--catalog`, `--config show`,
`--spec`, `--info`), so the command stays one click command with one parse.
What each mode costs is why they live in separate modules: the callback
imports the one it was given and nothing else, so that no mode pays for what it
did not ask for -- `--spec` imports every installed adapter, and is the clearest
case of a cost that must not reach the query path.

The names below are the exception, because the command has to know them to
build itself -- they are what `--config`'s choices are -- and a name is not an
import.
"""

from __future__ import annotations

INIT = "init"
"""The one `--config MODE` that writes rather than reports.

Named here because the command has to know it apart from the rest before it is
built: it is the mode that needs an adapter's options on it and no profile."""

CONFIG_MODES = ("show", "list-profiles", "validate", "schema", INIT)
"""Every `--config MODE`, in the order `--help` lists them.

The four that report come first and the one that writes last; a mode that is
not here is one click refuses by name, with the ones that do work in the
message.
"""
