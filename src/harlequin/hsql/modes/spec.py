"""`--spec`: the installed CLI surface, as data, so nobody has to parse `--help`.

`--help` is written for a person: it wraps to the terminal, it groups by
sympathy rather than by rule, and it says `[default: 500]` in prose. A caller
that wants to know whether `--limit` takes a number, what `--format` will
accept, or which flags an adapter it has never seen declares, should not have
to read English to find out. This writes the same facts as JSON, once, and an
agent that reads it does not have to guess at a second invocation.

**Two sources, one vocabulary.** hsql's own options are click parameters; an
adapter's are `AbstractOption`s, read through `to_dict()`. They are reported
under the same keys, in the CLI's terms: `name` is what a parameter is called
(and so what its key is in a profile), `decls` is every way to spell it on a
command line, and `default` is what the command uses when it is not passed.
That last one is why a flag reads `false` here whatever it declares -- a flag
that is absent is false, and this document is about the command line.

**It says which options must not be typed**: `secret` carries the adapter's
declaration through, so a caller learns that `--md-token` exists and that a
command line is the wrong place to put its value.

**It costs every adapter, and no connection.** Reading one adapter's options
means importing it; reporting all of them means importing all of them, which is
~300ms and rising with what is installed. That is the right trade for a
once-per-task lookup and the wrong one for anything on the query path, which is
why this module is imported by the callback only when `--spec` is passed.
`-a NAME` narrows the document to one adapter, and the import to one with it.

**What it cannot cover** is the `harlequin` command's own flags: building the
IDE's command means importing `harlequin.cli`, which `hsql` may never do. The
document says so under `scope` rather than presenting a list that looks
exhaustive and is not.
"""

from __future__ import annotations

import json
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, BinaryIO

import click

from harlequin.config import sluggify_option_name
from harlequin.exception import HarlequinConfigError
from harlequin.hsql import diagnostics
from harlequin.hsql.diagnostics import ExitCode
from harlequin.redact import REDACTED

if TYPE_CHECKING:
    from harlequin.options import AbstractOption

FILENAME = "spec.json"
"""What this document is called when `-o` names a folder to write it into."""

JSON = "json"
"""The one `--format` a document mode answers to. `none` writes nothing."""

NONE = "none"

SCOPE = (
    "hsql's own options and the connection options every installed adapter "
    "declares. The harlequin command's own options are not here -- run "
    "`harlequin --help` for those."
)
"""Said out loud, because a list that looks exhaustive and is not is worse than
a shorter list that says where it stops."""

TYPES = {
    # an adapter's option types, as `AbstractOption.to_dict()` reports them
    "flag": "boolean",
    "text": "text",
    "list": "text",
    "path": "path",
    "select": "choice",
    # click's, for hsql's own parameters
    "boolean": "boolean",
    "choice": "choice",
    "file": "path",
    "float": "number",
    "float range": "number",
    "integer": "integer",
    "integer range": "integer",
}
"""One vocabulary over two, so a reader does not have to learn which half of the
document a key came from. A type in neither column is reported as it was named,
which is what an adapter that defines its own option type gets."""


def report(
    out: BinaryIO,
    *,
    adapter: str | None,
    format_name: str,
    format_chosen: bool,
) -> ExitCode:
    """Write the spec, and return the code it exits with.

    Exit 0 whatever it found. An adapter that will not import is reported as
    one that will not import -- with a line on stderr and a null option list --
    rather than taken as a reason to answer nothing: the rest of the document
    is the part the caller asked for, and it is still true.
    """
    if format_name == NONE:
        return ExitCode.OK
    if format_name != JSON and format_chosen:
        diagnostics.report_document_format_ignored("--spec", format_name)

    command = _hsql_command()
    document = {
        "program": "hsql",
        "version": version("harlequin"),
        "scope": SCOPE,
        **command_document(command),
        "adapters": _adapters(adapter, command),
    }
    # `default=str` for a default no JSON type covers -- a Path, most likely,
    # from an option declared with one
    out.write((json.dumps(document, indent=2, default=str) + "\n").encode("utf-8"))
    return ExitCode.OK


def command_document(command: click.Command) -> dict[str, Any]:
    """One command's own arguments and options, in this document's vocabulary.

    The half of the spec that needs no adapter, so a caller that wants the
    command rather than an installation -- `scripts/write_cli_reference.py`,
    which describes the CLI for readers whose machines we know nothing about --
    gets it without importing one.
    """
    # `get_params` rather than `params`, because click keeps `--help` out of the
    # latter and adds it at parse time -- and a caller reading this to learn the
    # surface should be told about the flag that would have shown it to them
    ctx = click.Context(command, info_name="hsql")
    params = command.get_params(ctx)
    help_option = command.get_help_option(ctx)
    return {
        "arguments": [
            _from_argument(param)
            for param in params
            if isinstance(param, click.Argument)
        ],
        "options": sorted(
            (
                _from_parameter(param, name=_name_of(param, help_option))
                for param in params
                if not isinstance(param, click.Argument)
            ),
            key=lambda entry: str(entry["name"]),
        ),
    }


def _name_of(param: click.Parameter, help_option: click.Option | None) -> str:
    """What a caller calls a parameter, which for `--help` is `help`.

    click stores the help flag's value under a reserved name of its own, so that
    a command may declare a parameter called `help`. hsql does not, and a
    document that named the flag anything but `help` would be reporting click's
    bookkeeping rather than the CLI.
    """
    return "help" if param is help_option else str(param.name)


def _hsql_command() -> click.Command:
    """hsql's own surface, with no adapter's options mixed into it.

    The bare command rather than the one this invocation is running inside, so
    that `hsql --spec` and `hsql --spec -a duckdb` report the same options and
    differ only where they were asked to: in `adapters`.
    """
    from harlequin.hsql.cli import bare_command

    return bare_command()


def _adapters(only: str | None, command: click.Command) -> dict[str, Any]:
    """Every installed adapter's options, or one's, keyed by adapter name.

    Keyed rather than a list, because the question a caller has is about a name
    they already hold: what does `duckdb` take.
    """
    from harlequin.first_pass import command_spellings
    from harlequin.plugins import adapter_names, adapter_versions, load_adapter

    reserved, taken = command_spellings(command)
    versions = adapter_versions()
    names = [only] if only is not None else adapter_names()
    adapters: dict[str, Any] = {}
    for name in names:
        options: list[dict[str, Any]] | None = None
        error: str | None = None
        try:
            declared = load_adapter(name).ADAPTER_OPTIONS
        except HarlequinConfigError as e:
            # an adapter that is installed and will not import. Both halves are
            # worth reporting: an agent that reads only the document sees why
            # the options are missing, and a human sees it on stderr.
            error = e.msg
            diagnostics.note(f"{name} is installed, but could not be imported.")
        else:
            entries = (
                _from_option(option, reserved=reserved, taken=taken)
                for option in declared or []
            )
            options = sorted(
                (entry for entry in entries if entry is not None),
                key=lambda entry: str(entry["name"]),
            )
        adapters[name] = {
            "version": versions.get(name),
            "options": options,
            "error": error,
        }
    return adapters


def _from_option(
    option: AbstractOption, *, reserved: set[str], taken: set[str]
) -> dict[str, Any] | None:
    """One adapter option, in the same shape as one of hsql's own.

    None where hsql's own flags leave it nothing to be spelled with: this
    reports the surface a caller can type, and an option the command drops is
    not part of it.

    `name` is sluggified because that is what click makes of `--read-only` and
    what a profile writes it as, and `default` is what the command line does
    when the flag is absent -- false for a flag, however it declares itself for
    a GUI.

    `secret` is the key that teaches an agent reading this not to construct
    `hsql --password hunter2`: the flag exists, and a command line is the one
    place its value must not be typed, where `ps` and a shell history can read
    it.
    """
    declared = option.to_dict()
    name = sluggify_option_name(str(declared["name"]))
    decls = [
        decl
        for decl in (f"--{declared['name']}", *declared["short_decls"])
        if decl not in reserved
    ]
    if not decls or name in taken:
        return None
    is_flag = declared["type"] == "flag"
    secret = bool(declared["secret"])
    default = False if is_flag else declared["default"]
    if secret and isinstance(default, str) and default:
        # an adapter that ships a default for a secret has shipped the secret,
        # and this document is the one place a reader would find it written
        # down for every installation at once
        default = REDACTED
    # `metavar`, `required` and `envvar` are constants: `to_click()` passes
    # none of the three, so no adapter option has one
    return {
        "name": name,
        "decls": decls,
        "type": TYPES.get(str(declared["type"]), str(declared["type"])),
        "metavar": None,
        "choices": declared["choices"],
        "default": default,
        "multiple": declared["multiple"],
        "is_flag": is_flag,
        "required": False,
        "envvar": None,
        "secret": secret,
        "help": declared["description"],
    }


def _from_parameter(param: click.Parameter, *, name: str) -> dict[str, Any]:
    """One of hsql's own options, as click holds it."""
    return {
        "name": name,
        "decls": [*param.opts, *param.secondary_opts],
        "type": TYPES.get(param.type.name, param.type.name),
        "metavar": param.metavar,
        "choices": _choices(param),
        "default": _default(param),
        "multiple": bool(getattr(param, "multiple", False)),
        "is_flag": bool(getattr(param, "is_flag", False)),
        "required": bool(param.required),
        "envvar": _envvar(param),
        "secret": False,
        "help": getattr(param, "help", None),
    }


def _from_argument(param: click.Parameter) -> dict[str, Any]:
    """A positional, which has spellings for nothing and a place instead.

    `nargs` is the whole of what a caller needs here: -1 is "as many as you
    like", which is what `hsql db.duckdb` and `hsql host port` both are.
    """
    return {
        "name": param.name,
        "metavar": param.metavar or str(param.name).upper(),
        "type": TYPES.get(param.type.name, param.type.name),
        "nargs": param.nargs,
        "required": bool(param.required),
    }


def _choices(param: click.Parameter) -> list[str] | None:
    choices = getattr(param.type, "choices", None)
    return None if choices is None else [str(choice) for choice in choices]


def _default(param: click.Parameter) -> Any:
    """What the command uses when the parameter is absent, or null for nothing.

    Null covers three cases that are one answer: click's sentinel for an option
    declared without a default, a callable default (calling it here could read
    the environment, and a spec that varied with it is one nobody could cache),
    and anything else JSON has no room for.

    Through `to_info_dict()` because click derives a flag's default rather than
    storing one, and that is the method that resolves it -- to the `false` a
    flag reads here.
    """
    default = param.to_info_dict()["default"]
    if default is None or isinstance(default, (str, int, float, bool)):
        return default
    if isinstance(default, (list, tuple)):
        return list(default)
    return None


def _envvar(param: click.Parameter) -> str | list[str] | None:
    envvar = param.envvar
    if envvar is None or isinstance(envvar, str):
        return envvar
    return [str(name) for name in envvar]
