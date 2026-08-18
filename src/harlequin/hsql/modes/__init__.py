"""One module per `hsql` mode, imported only when that mode is asked for.

A mode is an option rather than a subcommand (`--config show`, `--spec`, and
later `--catalog` and `--info`), so the command stays one click command with one
parse. What each mode costs is why they live in separate modules: the callback
imports the one it was given and nothing else, so that no mode pays for what it
did not ask for -- `--spec` imports every installed adapter, and is the clearest
case of a cost that must not reach the query path.

The names below are the exception, because the command has to know them to
build itself -- they are what `--config`'s choices are -- and a name is not an
import.
"""

from __future__ import annotations

CONFIG_MODES = ("show", "list-profiles", "validate")
"""Every `--config MODE`, in the order `--help` lists them.

`schema` and `init` join this list as they land; a mode that is not here is one
click refuses by name, with the ones that do work in the message.
"""
