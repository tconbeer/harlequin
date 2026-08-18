"""`--config MODE`: what the config files say, and which file said it.

Two questions, and they are the two a caller actually has. `list-profiles`
answers *what can I pass to `-P`* -- a short list of names, so a profile that a
nearer file quietly displaced is visible as a name that is missing. `show`
answers *which file is winning* -- the merged document, with the file each
value came from written beside it, and the files it overrode written after
that.

Neither connects, and neither imports an adapter: a mode that reports on config
files must work when the database does not. `show` does not import the
execution core either -- it writes a document, not rows -- which is why the two
imports `list-profiles` needs are deferred into it.

The renderings are the merge, told two ways: TOML for a person, because that is
the language they will edit the answer in, and JSON for a caller, under the same
`--format json` that means JSON everywhere else in this command.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, BinaryIO, Mapping, Sequence

from harlequin.config import Provenance, load_config
from harlequin.hsql import diagnostics
from harlequin.hsql.modes import CONFIG_MODES

if TYPE_CHECKING:
    from pathlib import Path

    from tomlkit.items import Item, Table

    from harlequin.config import Config
    from harlequin.layout import LayoutOptions

SHOW, LIST_PROFILES = CONFIG_MODES

JSON = "json"
"""The one `--format` a document mode answers to. `none` writes nothing."""

NONE = "none"


def report(
    mode: str,
    out: BinaryIO,
    *,
    config_path: Path | None,
    format_name: str,
    format_chosen: bool,
    layout_options: LayoutOptions,
    file_options: Mapping[str, Any],
) -> None:
    """Write one `--config` mode's answer to an open binary stream.

    Raises: HarlequinConfigError if a discovered config file cannot be read.
    Every mode here reads every file, so a broken one is reported rather than
    skipped -- `--config validate` is the mode that reports all of them at once.
    """
    provenance = Provenance()
    config = load_config(config_path, provenance=provenance)

    if mode == SHOW:
        _write_document(
            config,
            provenance,
            out,
            mode=mode,
            format_name=format_name,
            format_chosen=format_chosen,
        )
    else:
        _write_profiles(
            config,
            out,
            format_name=format_name,
            layout_options=layout_options,
            file_options=file_options,
        )


def _write_document(
    config: Config,
    provenance: Provenance,
    out: BinaryIO,
    *,
    mode: str,
    format_name: str,
    format_chosen: bool,
) -> None:
    """The merged config, as TOML or as JSON, and nothing in between.

    A document is not rows, so the formats that arrange rows have nothing to
    arrange here. `--format json` reaches this one because JSON is a document
    too; every other format is declined with a line on stderr rather than
    silently, and `none` means what it always means.
    """
    if format_name == NONE:
        return
    if format_name == JSON:
        out.write(_as_json(config, provenance).encode("utf-8"))
        return
    if format_chosen:
        diagnostics.report_document_format_ignored(f"--config {mode}", format_name)
    out.write(_as_toml(config, provenance).encode("utf-8"))


def _write_profiles(
    config: Config,
    out: BinaryIO,
    *,
    format_name: str,
    layout_options: LayoutOptions,
    file_options: Mapping[str, Any],
) -> None:
    """The profiles, as rows, through the same writer a query's results take.

    Sorted by name: two profiles with different names do not compete, so the
    order the files happened to define them in carries no information, and a
    list a caller reads twice should read the same way both times.
    """
    # deferred: this is the only mode here that produces rows, and the row
    # machinery is pyarrow. `--config show` writes a document and must not pay
    # for it.
    from harlequin.hsql import output
    from harlequin.query import rows_to_result

    default = config.default_profile
    if default is not None and default not in config.profiles:
        # not an error: a `default_profile` naming nothing is only fatal for an
        # invocation that was going to use it, and this one is not. It is
        # exactly what this mode exists to make visible, though.
        diagnostics.note(
            f"default_profile is {default!r}, which no config file defines."
        )

    rows = [
        (name, _text(profile.get("adapter")), "true" if name == default else "false")
        for name, profile in sorted(config.profiles.items())
    ]
    output.write(
        rows_to_result(["profile", "adapter", "default"], rows),
        format_name,
        out,
        layout_options=layout_options,
        file_options=file_options,
    )


def _text(value: Any) -> str | None:
    """A profile's value as a string, or null where the profile has none.

    Null rather than hsql's own default: this reports what the files say, and a
    profile that names no adapter is one `-a` still decides.
    """
    return None if value is None else str(value)


def _as_toml(config: Config, provenance: Provenance) -> str:
    """The merged config, in the language it was written in, annotated.

    Through tomlkit, the same writer `harlequin --config` edits a user's file
    with, because everything fiddly here is something it already owns: quoting
    a key with a dot in it, escaping a string, spelling a date the way TOML
    reads it back. Deferred rather than imported at the top, so that the two
    answers that are not TOML -- `list-profiles`, and `show --json` -- do not
    pay ~30ms for a parser they never call.
    """
    import tomlkit

    document = tomlkit.document()
    if not provenance.files:
        document.add(tomlkit.comment("No config file defines anything hsql reads."))
        return tomlkit.dumps(document)

    if provenance.default_profile:
        document["default_profile"] = _from(
            tomlkit.item(config.default_profile), provenance.default_profile
        )
    if config.profiles:
        profiles = tomlkit.table(is_super_table=True)
        for name in sorted(config.profiles):
            profiles[name] = _from(
                _as_table(config.profiles[name]), provenance.profiles[name]
            )
        document["profiles"] = profiles
    if config.keymaps:
        keymaps = tomlkit.table(is_super_table=True)
        for name in sorted(config.keymaps):
            bindings = tomlkit.aot()
            for binding in config.keymaps[name]:
                bindings.append(_as_table(binding))
            if bindings:
                # the array comes from one file whole, so its provenance is
                # written once rather than over every binding in it
                _from(bindings[0], provenance.keymaps[name])
            keymaps[name] = bindings
        document["keymaps"] = keymaps
    return tomlkit.dumps(document)


def _as_table(values: Mapping[str, Any]) -> Table:
    """One `[profiles.x]`, in the order the file that defined it wrote it."""
    import tomlkit

    table = tomlkit.table()
    for key, value in values.items():
        table[key] = value
    return table


def _from(item: Item, files: Sequence[Path]) -> Item:
    """Write `# from <file>` on an item, and what that file's value displaced."""
    winner, *overrode = files
    comment = f"from {winner}"
    if overrode:
        comment += ", overriding " + ", ".join(str(path) for path in overrode)
    item.comment(comment)
    return item


def _as_json(config: Config, provenance: Provenance) -> str:
    """The same three keys, for a caller that would rather not parse TOML.

    The shape is stable whatever the files hold: a key nothing defines is
    `null`, not a key that is missing.
    """
    document = {
        "default_profile": _entry(config.default_profile, provenance.default_profile),
        "profiles": {
            name: _entry(config.profiles[name], provenance.profiles[name])
            for name in sorted(config.profiles)
        },
        "keymaps": {
            name: _entry(config.keymaps[name], provenance.keymaps[name])
            for name in sorted(config.keymaps)
        },
    }
    # `default=str` for TOML's own date and time types, which JSON has none of
    return json.dumps(document, indent=2, default=str) + "\n"


def _entry(value: Any, files: Sequence[Path]) -> dict[str, Any] | None:
    """One merged value, the file it came from, and the files it beat."""
    if not files:
        return None
    winner, *overrode = files
    return {
        "value": value,
        "from": str(winner),
        "overrode": [str(path) for path in overrode],
    }
