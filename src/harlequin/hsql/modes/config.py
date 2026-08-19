"""`--config MODE`: what the config files say, which said it, what may be in one
-- and, for `init`, one more profile in one of them.

Four questions, and they are the four a caller actually has. `list-profiles`
answers *what can I pass to `-P`* -- a short list of names, so a profile that a
nearer file quietly displaced is visible as a name that is missing. `show`
answers *which file is winning* -- the merged document, with the file each
value came from written beside it, and the files it overrode written after
that. `validate` answers *what is wrong with any of it* -- every problem in
every file, one to a row, where a run would have stopped at the first. `schema`
answers *what may I write* -- a JSON Schema for a config file, built from this
installation, so an editor or an agent can answer the rest of them itself.

`init` is the fifth, and the only one that changes anything: it writes a
profile into a config file, through the same tomlkit path `harlequin --config`
edits one with, so the comments and key order around it survive. It prompts for
nothing, which is the whole point of having it beside a wizard -- each command
gets the affordance right for its audience, and `hsql` may not import
questionary.

None of them connects, and `schema` does not even read a file: it describes the
shape a config file may take, whether or not this machine has one. `show` and
`list-profiles` import no adapter either -- a mode that reports on config files
must work when the database does not. The other three do: `validate` imports one
per adapter its profiles name, `schema` every installed one, and `init` the one
whose options it is writing, because all three are describing options only the
adapter declares. `show` does not import the execution core either -- it writes
a document, not rows -- which is why the two imports the row-shaped modes need
are deferred into them.

The renderings are the merge, told two ways: TOML for a person, because that is
the language they will edit the answer in, and JSON for a caller, under the same
`--format json` that means JSON everywhere else in this command.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Callable, Mapping, Sequence

from harlequin.config import (
    ConfigFile,
    Provenance,
    get_highest_priority_existing_config_file,
    load_config,
)
from harlequin.exception import HarlequinConfigError
from harlequin.hsql import diagnostics
from harlequin.hsql.diagnostics import ExitCode
from harlequin.hsql.modes import CONFIG_MODES

if TYPE_CHECKING:
    from tomlkit.items import Item, Table

    from harlequin.config import Config
    from harlequin.layout import LayoutOptions
    from harlequin.options import AbstractOption

SHOW, LIST_PROFILES, VALIDATE, SCHEMA, INIT = CONFIG_MODES

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
) -> ExitCode:
    """Write one `--config` mode's answer, and return the code it exits with.

    A code rather than nothing, because one of these modes is a check: a script
    that runs `hsql --config validate` wants the answer without reading it, and
    an exit code is how it gets one. The others exit 0 whatever they found --
    reporting a config is not judging it.

    Raises: HarlequinConfigError if a discovered config file cannot be read.
    `show` and `list-profiles` read every file, so a broken one is fatal to
    them; `validate` is the mode that reports it as one problem among however
    many the rest of the files hold.
    """
    if mode == SCHEMA:
        _write_schema(out, format_name=format_name, format_chosen=format_chosen)
        return ExitCode.OK

    if mode == VALIDATE:
        return _write_problems(
            config_path,
            out,
            format_name=format_name,
            layout_options=layout_options,
            file_options=file_options,
        )

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
    return ExitCode.OK


def initialize(
    *,
    profile_name: str | None,
    adapter: str,
    values: Mapping[str, Any],
    config_path: Path | None,
) -> Path:
    """Write one profile into a config file, and return the file it went into.

    `values` is what the caller typed and nothing else, so the profile says what
    they asked for rather than restating every default the command already has.
    The profile is written whole: a key it does not name is one the file no
    longer has, which is what `harlequin --config` does with the profile it
    edits and the only reading of `init` that does not leave a caller guessing
    at what survived.

    Raises: HarlequinConfigError for a name no profile may have, a destination
    that is not TOML, or a file this cannot parse -- and OSError for one it
    cannot write.
    """
    if not profile_name:
        raise HarlequinConfigError(
            "--config init writes a profile, so it needs a name for one: pass -P NAME.",
            title="Harlequin could not write your configuration.",
        )
    if profile_name == "None":
        raise HarlequinConfigError(
            "-P None is how a caller asks for no profile at all, so it cannot "
            "name the one --config init writes. Pass another name.",
            title="Harlequin could not write your configuration.",
        )
    destination = config_path if config_path is not None else _destination()
    if destination.suffix != ".toml":
        raise HarlequinConfigError(
            f"A config file must be TOML, and {destination} is not a .toml file.",
            title="Harlequin could not write your configuration.",
        )

    # the whole file's Harlequin section, edited and handed back: `update()`
    # prunes each table it is given against what it is given, so the profiles
    # this is not writing have to be among them to survive.
    config_file = ConfigFile(destination)
    config = config_file.relevant_config
    profiles = config.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        # nothing has validated this file -- the write path reads it raw, so
        # that what it writes back is what the user wrote -- and a `profiles`
        # that is not a table is a file to refuse rather than to rewrite
        raise HarlequinConfigError(
            f"{destination} has a profiles key that is not a table of profiles, "
            "so there is nothing to write a profile into.",
            title="Harlequin could not write your configuration.",
        )
    replaced = profile_name in profiles
    profiles[profile_name] = {
        "adapter": adapter,
        # TOML has no null, so a key with no value is a key the file cannot
        # have. Nothing here is at its default, so this only ever drops an
        # option a caller set to nothing.
        **{key: _writable(value) for key, value in values.items() if value is not None},
    }
    config_file.update(config)
    config_file.write()
    # on stderr, where everything this command says about itself goes: stdout
    # is data, and a mode that writes a file produces none
    diagnostics.note(
        f"replaced the profile named {profile_name} in {destination}."
        if replaced
        else f"wrote the profile named {profile_name} to {destination}."
    )
    return destination


def _destination() -> Path:
    """The file `init` writes when nothing named one.

    The nearest config file that exists, which is where the wizard starts too,
    and a new `.harlequin.toml` in the working directory when there is none: a
    caller with no config yet gets one beside the project they are configuring,
    rather than in a home directory they did not mention.
    """
    existing = get_highest_priority_existing_config_file()
    return existing if existing is not None else Path.cwd() / ".harlequin.toml"


def _writable(value: Any) -> Any:
    """One click value as the TOML it is written as.

    A repeatable option arrives as a tuple and a path as a `Path`, and TOML has
    an array and a string. Everything else is already what it will be written
    as, because the option's declared type is what click cast it to.
    """
    if isinstance(value, (tuple, list)):
        return [_writable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


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


def _write_schema(out: BinaryIO, *, format_name: str, format_chosen: bool) -> None:
    """A JSON Schema for a config file, describing the adapters installed here.

    JSON whatever `--format` says, because a JSON Schema is JSON: a format that
    arranges rows has nothing to arrange, and one that is not JSON is declined
    with a line on stderr rather than silently.
    """
    if format_name == NONE:
        return
    if format_name != JSON and format_chosen:
        diagnostics.report_document_format_ignored(f"--config {SCHEMA}", format_name)

    # deferred, and this is the mode that pays for it: every installed adapter,
    # for the options only the adapter declares. Nothing else in this module
    # imports more than one.
    from harlequin.config_schema import build_schema
    from harlequin.hsql.cli import bare_command

    document = build_schema(bare_command().params, _installed_adapters())
    out.write((json.dumps(document, indent=2) + "\n").encode("utf-8"))


def _installed_adapters() -> dict[str, Sequence[AbstractOption] | None]:
    """What each installed adapter declares, or None where it would not import.

    None rather than nothing, so the schema can leave a profile that names that
    adapter open instead of calling every key in it an error.
    """
    from harlequin.plugins import adapter_names, load_adapter

    declared: dict[str, Sequence[AbstractOption] | None] = {}
    for name in adapter_names():
        try:
            options = load_adapter(name).ADAPTER_OPTIONS
        except HarlequinConfigError:
            diagnostics.note(f"{name} is installed, but could not be imported.")
            declared[name] = None
        else:
            declared[name] = list(options or [])
    return declared


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


def _write_problems(
    config_path: Path | None,
    out: BinaryIO,
    *,
    format_name: str,
    layout_options: LayoutOptions,
    file_options: Mapping[str, Any],
) -> ExitCode:
    """Every problem in every config file, as rows, and a code that says so.

    Rows because a problem is four facts -- the file, the key it is written
    under, what is wrong, and the line where the parser knew one -- and four
    facts in a shape is what `-tA` and `--json` are already for. Ordered by
    file, nearest first, because a report about files is one a reader walks
    through alongside them.

    Exit 2 when there is anything to report, which is the code a config problem
    already has. That is what makes this mode usable without reading it:
    `hsql --config validate --format none` is the whole check.
    """
    # deferred, each for its own reason: the row machinery is pyarrow, which
    # `--config show` must not pay for, and `validate_config_files` reaches for
    # an adapter per profile, which nothing else in this module does.
    from harlequin.config import validate_config_files
    from harlequin.hsql import output
    from harlequin.query import rows_to_result

    provenance = Provenance()
    problems = validate_config_files(
        config_path,
        adapter_options=_adapter_options(),
        command_options=_command_options(),
        provenance=provenance,
    )
    if not problems and not provenance.files:
        # a clean report and no config files read the same on stdout, and they
        # are not the same answer. The one that found nothing to check says so.
        diagnostics.note("No config file defines anything hsql reads.")

    rows = [
        (
            str(problem.path),
            problem.key,
            problem.message,
            None if problem.line is None else str(problem.line),
        )
        for problem in problems
    ]
    output.write(
        rows_to_result(["file", "key", "problem", "line"], rows),
        format_name,
        out,
        layout_options=layout_options,
        file_options=file_options,
    )
    return ExitCode.USAGE if problems else ExitCode.OK


def _adapter_options() -> Callable[[str], Sequence[AbstractOption] | None]:
    """A way to ask what one adapter declares, importing each of them once.

    The cache is per invocation rather than per process: four profiles naming
    duckdb is one import, and a second call in the same interpreter -- which is
    every test, and no `hsql` -- gets to see whatever is installed then.
    """
    from harlequin.plugins import load_adapter

    declared: dict[str, Sequence[AbstractOption] | None] = {}

    def options(name: str) -> Sequence[AbstractOption] | None:
        if name not in declared:
            # raises for a name nothing installed provides, which the validator
            # records against the profile that named it
            declared[name] = load_adapter(name).ADAPTER_OPTIONS
        return declared[name]

    return options


def _command_options() -> set[str]:
    """Every profile key a command reads for itself, so neither is an adapter's.

    hsql's own, from the command it builds when it is asked for nothing, plus
    the IDE's -- one profile serves both, and a profile written for the IDE has
    to validate here or `--config validate` would report the other command's
    config as broken.
    """
    from harlequin.config import TUI_ONLY_KEYS
    from harlequin.hsql.cli import bare_command

    return {param.name for param in bare_command().params if param.name} | set(
        TUI_ONLY_KEYS
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
