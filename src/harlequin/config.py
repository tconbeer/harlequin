"""Reading the TOML files both commands take their options from.

Config files are discovered nearest first -- an explicit `--config-path`, then
the working directory, the user config dir, and home -- and the first file to
define something is the one that defines it.

Who reads what:

| caller                 | function                     | files read           |
| ---------------------- | ---------------------------- | -------------------- |
| `hsql`                 | `load_profile()`             | up to the one that   |
|                        |                              | defines the profile  |
| `hsql --info`          | `resolve_profile()`          | the same, plus the   |
|                        |                              | name it resolved     |
| `harlequin`, `--keys`  | `load_profile_and_keymaps()` | all: keymaps merge   |
|                        |                              | across every file    |
| the IDE's debug screen | `load_config()`              | all                  |
| `hsql --config show`   | `load_config()`, with a      | all                  |
|                        | `Provenance` to fill in      |                      |
| `hsql --config`        | `validate_config_files()`    | all, and none of     |
| `validate`             |                              | them fatally         |
| `harlequin --config`,  | `ConfigFile`, at the path    | one, and unvalidated |
| `hsql --config init`   | `get_highest_priority_...()` | (it is about to be   |
|                        | returns, or `--config-path`  | written back)        |

`load_profile()` is the fast path: it stops at the file that answers the
question, so `hsql -P prod` never opens the files behind that one, and
`hsql -P None` opens none. `resolve_profile()` is the same walk, returning the
name it resolved beside the profile. The others need the whole document.

A profile's strings have their `${VAR}`s resolved from the environment as the
profile is selected -- not as its file is read, so that an invocation is never
refused over a variable named in a profile it is not running, and not in
`ConfigFile.relevant_config`, which is what the write path edits and hands
back: a `${MYPASSWORD}` a user wrote is still a `${MYPASSWORD}` after
`harlequin --config` has rewritten the file around it.

Config files are validated twice, and the halves know different things.
`_read_config_files()` checks one file's shape before it is merged with any
other, so the error can name the file. `parse_profile_options()` runs once the
adapter is known and checks the profile's remaining keys -- each of which is
some adapter's option -- against the options that adapter declares.

Both passes raise at the first problem, because every caller but one is on its
way to a database. Hand them a `Problems` and they record it and carry on
instead, which is the whole of `--config validate`.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Collection,
    Container,
    Dict,
    Iterator,
    List,
    Literal,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    cast,
)

import msgspec
from platformdirs import user_config_path

from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyMap, RawKeyBinding

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

if TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

    from harlequin.options import AbstractOption

DEFAULT_ADAPTER = "duckdb"
"""The adapter both commands connect with when nothing names one."""

CONFIG_ERROR_TITLE = "Harlequin couldn't load your config file."

UNLIMITED = -1
"""What every row-count option takes to mean "no limit".

Not 0, which asks for zero rows: `select * from t` under `limit = 0` is how a
caller asks a database what a query's columns are, and an option that spent
that spelling on "unlimited" would take the idiom away.
"""

TUI_ONLY_KEYS = (
    "theme",
    "keymap_name",
    "show_files",
    "show_s3",
    "locale",
    "no_download_tzdata",
    "viewer_max_rows",
)
"""Profile keys the IDE reads and a headless caller must drop.

One profile serves both commands, so a profile written for the IDE has to work
headless -- these are dropped rather than handed to an adapter as options it
never declared. `locale` in particular is one a headless caller must ignore:
the IDE sets it to group digits for a human, and output that varied with
`LC_ALL` would be output a caller could not predict.
"""

Profile = Dict[str, Any]
"""One `[profiles.x]` table: a command's own options, plus its adapter's.

Deliberately open. Which keys belong to the adapter, and what they may hold, is
`parse_profile_options()`'s question, and it takes the adapter to answer.
"""


@dataclass
class Provenance:
    """Which config file each of a merged config's values came from.

    Keyed the way the merge is keyed (`_merge()`): `default_profile` whole, and
    one entry per profile and per keymap name. A profile is supplied whole by
    the nearest file that defines it, so a key *inside* one has no provenance of
    its own -- which is what makes this a short document rather than one line
    per key.

    Each list holds every file that defined that name, nearest first: the first
    is the file whose definition won, and the rest are the ones it overrode.
    Filled in by the merge for the caller that asks (`--config show`), so that
    the callers that do not ask -- every start-up -- pay nothing for it.
    """

    files: list[Path] = field(default_factory=list)
    """Every config file that defined something, nearest first.

    Not every file that was opened: a `pyproject.toml` with no section of ours
    is in the working directory of every Python project, and naming it would
    send a reader to a file with nothing of theirs in it.
    """

    default_profile: list[Path] = field(default_factory=list)
    profiles: dict[str, list[Path]] = field(default_factory=dict)
    keymaps: dict[str, list[Path]] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfigProblem:
    """One thing wrong with one config file, as a row of a report.

    `key` is the dotted path the problem is written under
    (`profiles.prod.reed_only`), and None for a problem with the file rather
    than with something in it. `line` is only ever a line the TOML parser
    named: nothing reports a position for a key we merely dislike, and a
    made-up number is worse than no number.
    """

    path: Path
    key: str | None
    message: str
    line: int | None = None


@dataclass
class Problems:
    """Where a reader puts a problem instead of raising it.

    Passed the way `Provenance` is, and for the same reason: a reader given one
    records what it finds and reads on, a reader given none raises at the first
    thing it cannot use, and neither has a second copy of what it checks.
    `--config validate` is the only caller that wants the first behavior.

    `at()` binds the file and the key an entry belongs under, so that the
    functions that find a problem do not have to know where they are.
    """

    items: list[ConfigProblem] = field(default_factory=list)
    path: Path | None = None
    key: str = ""

    def at(self, path: Path, key: str = "") -> Problems:
        """The same collector, for the problems in one file, under one key."""
        return Problems(items=self.items, path=path, key=key)

    def add(self, message: str, *, key: str = "", line: int | None = None) -> None:
        """Record one problem, folded onto one line, because it will be a row."""
        assert self.path is not None, "bind a collector to a file with at()"
        self.items.append(
            ConfigProblem(
                path=self.path,
                key=".".join(part for part in (self.key, key) if part) or None,
                message=" ".join(message.split()),
                line=line,
            )
        )


class Config(msgspec.Struct, forbid_unknown_fields=True):
    """A config file's contents, and also several of them merged.

    The only declaration of what a config file may say: `msgspec.convert()`
    validates against it, and refuses a key it does not name.
    """

    default_profile: Optional[str] = None
    profiles: Dict[str, Profile] = msgspec.field(default_factory=dict)
    keymaps: Dict[str, List[Dict[str, Any]]] = msgspec.field(default_factory=dict)
    """A binding's own keys are `HarlequinKeyMap.from_config()`'s to check."""

    def to_dict(self) -> dict[str, Any]:
        """What this config would look like written back to a file."""
        return {
            key: value
            for key, value in msgspec.structs.asdict(self).items()
            # the one thing TOML cannot write. Everything else is kept, however
            # falsy: `default_profile = ""` is a mistake worth being able to see.
            if value is not None
        }


class ConfigFile:
    """One TOML file on disk, read cheaply and written back without reflowing.

    Reading and writing use different parsers on purpose. Every start-up reads
    config -- including `hsql`, and including the `pyproject.toml` that happens
    to be in the working directory of any Python project -- so reads go through
    `tomllib`, which is C and about 30x faster than tomlkit on a file that size.

    Writes are rare (`harlequin --config`, and the keymap editor) and have a
    requirement reads do not: a user's comments, key order and quoting have to
    survive being rewritten. That is what tomlkit is for, so `update()` re-reads
    the file through it, and nothing imports tomlkit until something writes.
    """

    def __init__(self, path: Path, problems: Problems | None = None) -> None:
        """
        Opens and reads the TOML file at path. Can be used to create
        a new file if one does not already exist.

        Raises: HarlequinConfigError if we can't read the TOML file, unless a
        `Problems` is there to record it instead, in which case the file reads
        as the empty config it is to every caller downstream.
        """
        self.path = path
        self.is_pyproject = path.stem == "pyproject"
        self._doc: TOMLDocument | None = None
        self._data: dict[str, Any] = {}
        try:
            with open(path, "rb") as f:
                self._data = tomllib.load(f)
        except OSError:
            pass
        except tomllib.TOMLDecodeError as e:
            if problems is None:
                raise HarlequinConfigError(
                    f"Attempted to load the config file at {path}, but encountered an "
                    f"error:\n\n{e}",
                    title="Harlequin could not load the config file.",
                ) from e
            # the parser is the only thing in the stack that knows a line
            # number, and depending on its version it is either an attribute or
            # only ever in the message
            problems.at(path).add(
                str(e), line=getattr(e, "lineno", None) or _line_in(str(e))
            )

    @property
    def relevant_config(self) -> dict[str, Any]:
        """This file's Harlequin section, exactly as written.

        Unvalidated and uninterpolated, and the write path depends on both:
        `harlequin --config` reads this, edits it, and writes it back, so
        anything transformed on the way in would be written into the user's
        file on the way out -- a resolved `${MYPASSWORD}` most of all.
        """
        if not self.is_pyproject:
            return self._data
        section: dict[str, Any] = self._data.get("tool", {}).get("harlequin", {})
        return section

    def _editable(self) -> TOMLDocument:
        """The same file, re-read through tomlkit so a write keeps its style.

        Built on demand rather than in `__init__`: this is the expensive parse,
        and only the two commands that write a config file ever need it.
        """
        if self._doc is not None:
            return self._doc

        from tomlkit.exceptions import TOMLKitError
        from tomlkit.toml_document import TOMLDocument
        from tomlkit.toml_file import TOMLFile

        try:
            self._doc = TOMLFile(self.path).read()
        except OSError:
            self._doc = TOMLDocument()
        except TOMLKitError as e:
            # `__init__` already parsed this file, so reaching here means the
            # two parsers disagree about it -- rare, but not something to write
            # a half-understood file over.
            raise HarlequinConfigError(
                f"Attempted to load the config file at {self.path}, but encountered "
                f"an error:\n\n{e}",
                title="Harlequin could not load the config file.",
            ) from e
        return self._doc

    def update(self, config: Mapping[str, Any], *, whole_section: bool = False) -> None:
        """
        Merge the updated config into the relevant section of the in-memory
        TOML doc, key by key, so a table nobody edited keeps its own nodes --
        and with them the comments written inside it.

        `whole_section` says `config` is everything the section should say, so
        a top-level key missing from it is one the caller means to delete --
        which `harlequin --config` needs to turn a `default_profile` off. It is
        off by default, for the keymap editor, which passes `keymaps` alone.
        """
        doc = self._editable()
        if self.is_pyproject:
            if "tool" not in doc:
                doc["tool"] = {"harlequin": {}}
            elif "harlequin" not in doc["tool"]:
                doc["tool"]["harlequin"] = {}
            _merge_into_doc(doc["tool"]["harlequin"], config, prune=whole_section)
        else:
            _merge_into_doc(doc, config, prune=whole_section)

    def write(self) -> None:
        """
        Write the in-memory TOML doc to disk, at self.path.
        """
        from tomlkit.toml_file import TOMLFile

        self.path.parent.mkdir(parents=True, exist_ok=True)
        TOMLFile(self.path).write(self._editable())


def load_config(
    config_path: Path | None, provenance: Provenance | None = None
) -> Config:
    """Every discovered config file, merged nearest first.

    Pass a `Provenance` to learn which file each merged value came from: the
    merge fills it in as it goes, so the one caller that wants it (`hsql
    --config show`) gets it for free and every other caller pays nothing.
    """
    config = Config()
    for path, from_file in _read_config_files(config_path):
        _merge(from_file, into=config, source=path, provenance=provenance)
    return config


def load_profile(config_path: Path | None, profile_name: str | None) -> Profile:
    """The profile an invocation runs under, reading no further than it must."""
    return resolve_profile(config_path, profile_name)[1]


def resolve_profile(
    config_path: Path | None, profile_name: str | None
) -> tuple[str | None, Profile]:
    """The name an invocation's profile resolves to, and the profile itself.

    The name is what `-P` asked for, or the `default_profile` the files settled
    on, and None where nothing named one -- which `load_profile()` resolves and
    then discards, and `hsql --info` reports.

    Raises: HarlequinConfigError for a name no discovered file defines, or for
    a `${VAR}` in the profile it resolved to that the environment does not set.
    """
    if profile_name == "None":
        return "None", {}  # Harlequin's own defaults, which no config file can change

    config = Config()
    # an unset `${VAR}` names the file it is written in, and the file a profile
    # came from is not the file being read when its name resolves: a nearer
    # file can define `[profiles.prod]` and a farther one set the
    # `default_profile` that selects it
    provenance = Provenance()
    for path, from_file in _read_config_files(config_path):
        _merge(from_file, into=config, source=path, provenance=provenance)
        name = profile_name or config.default_profile
        if name is not None and name in config.profiles:
            # the files behind this one go unread
            return name, _select_profile(config, requested=name, provenance=provenance)
    return (
        profile_name or config.default_profile,
        _select_profile(config, requested=profile_name, provenance=provenance),
    )


def load_profile_and_keymaps(
    config_path: Path | None, profile_name: str | None
) -> tuple[Profile, list[HarlequinKeyMap]]:
    """One profile, and every keymap, for the IDE, which needs both.

    The provenance is what an unset `${VAR}` in the profile names the file it
    is written in from: once every file has been merged, it is the only thing
    that still knows which one that was.
    """
    provenance = Provenance()
    config = load_config(config_path, provenance=provenance)
    # a binding is a `RawKeyBinding` once `from_config()` has said so; the shape
    # of the table around it is all `Config` promises
    keymaps = [
        HarlequinKeyMap.from_config(
            name=keymap_name, bindings=cast("list[RawKeyBinding]", bindings)
        )
        for keymap_name, bindings in config.keymaps.items()
    ]
    return (
        _select_profile(config, requested=profile_name, provenance=provenance),
        keymaps,
    )


def validate_config_files(
    config_path: Path | None,
    *,
    adapter_options: Callable[[str], Sequence[AbstractOption] | None],
    command_options: Collection[str],
    provenance: Provenance | None = None,
) -> list[ConfigProblem]:
    """Every discovered config file, and everything wrong with any of them.

    The same two passes every start-up runs, with a `Problems` where a command
    would have had an exception: `_read_config_files()` checks each file's
    shape before the merge, and `parse_profile_options()` checks each of its
    profiles against the options that profile's adapter declares. Neither stops
    at the first problem, and both are the same code that raises for everyone
    else -- a second copy of either would sooner or later disagree with it.

    Per file rather than over the merge, which is what validating before
    merging buys: a profile a nearer file displaced is not what any invocation
    runs, and is still a table its author will edit. Every profile, for the
    same reason -- so this is the mode that reports a `${VAR}` the environment
    does not set, wherever it is written, rather than only in the one profile a
    run would have selected.

    `adapter_options` is the second pass's price: one adapter import per
    adapter a profile names. It may raise `HarlequinConfigError` for a name
    nothing installed provides, which is recorded against the profile that
    named it.

    Raises: HarlequinConfigError if `config_path` names a file that is not
    there -- the one problem that is not in a config file.
    """
    problems = Problems()
    provenance = provenance if provenance is not None else Provenance()
    merged = Config()
    for path, from_file in _read_config_files(config_path, problems=problems):
        for name, profile in from_file.profiles.items():
            _validate_profile(
                _interpolated(
                    profile,
                    path=path,
                    key=f"profiles.{name}",
                    problems=problems,
                ),
                adapter_options=adapter_options,
                command_options=command_options,
                problems=problems.at(path, f"profiles.{name}"),
            )
        for name, bindings in from_file.keymaps.items():
            _validate_keymap(
                name, bindings, problems=problems.at(path, f"keymaps.{name}")
            )
        _merge(from_file, into=merged, source=path, provenance=provenance)

    if merged.default_profile is not None:
        # the one problem no single file has, so the only one asked after the
        # merge -- and the file that is wrong about it is the one that set it
        _select_profile(
            merged,
            requested=None,
            problems=problems.at(provenance.default_profile[0]),
        )
    return problems.items


def parse_profile_options(
    profile: Profile,
    *,
    adapter: str,
    adapter_options: Sequence[AbstractOption] | None,
    command_options: Collection[str],
    problems: Problems | None = None,
) -> Profile:
    """The profile, with its adapter's options parsed as that adapter declares them.

    `command_options` names the keys a command reads for itself; every other
    key has to be an option of `adapter`, and this is the only place that can
    tell, since an adapter's constructor takes supersets of what it declares
    and drops the rest in silence.

    Raises: HarlequinConfigError, naming the option and the adapter -- unless a
    `Problems` is there to record every one of them instead, in which case the
    profile comes back the way it went in.
    """
    declared = {sluggify_option_name(o.name): o for o in adapter_options or []}
    given = {
        key: _as_declared(value, declared.get(key, None))
        for key, value in profile.items()
        if key not in command_options
    }
    if not given:
        return profile

    try:
        parsed = msgspec.convert(
            given, adapter_options_model(adapter, declared), strict=False
        )
    except msgspec.ValidationError as e:
        found = _option_problems(
            e, adapter=adapter, declared=declared, given=given, allowed=command_options
        )
        if problems is None:
            # the first of them: this caller is on its way to a connection, and
            # will not get to the second
            raise HarlequinConfigError(found[0][1], title=CONFIG_ERROR_TITLE) from e
        for key, message in found:
            problems.add(message, key=key)
        return profile

    return {**profile, **{key: getattr(parsed, key) for key in given}}


def adapter_options_model(
    adapter: str, declared: Mapping[str, AbstractOption]
) -> type[msgspec.Struct]:
    """One adapter's declared options, as a model a profile can be parsed into.

    Every field is optional: a profile sets the options it needs and leaves the
    rest to the adapter's own defaults.
    """
    fields: list[tuple[str, type, Any]] = [
        (name, Optional[_declared_type(option)], None)  # type: ignore[misc]
        for name, option in declared.items()
        if name.isidentifier()
    ]
    return msgspec.defstruct(f"{adapter}_options", fields, forbid_unknown_fields=True)


def merge_profile_with_cli(
    profile: Profile,
    cli_values: Mapping[str, Any],
    explicitly_set: Container[str],
) -> Profile:
    """
    Layer the CLI values a user typed over the profile from their config files.

    A value counts as typed only if `explicitly_set` names it -- in click, every
    parameter whose `get_parameter_source()` is not `DEFAULT`. An empty
    `conn_str` never counts: it is an argument, so click always reports it as
    typed.
    """
    merged: dict[str, Any] = dict(profile)
    for key, value in cli_values.items():
        if key not in explicitly_set:
            continue
        if key == "conn_str" and value == tuple():
            continue
        merged[key] = value
    return merged


def parse_row_count(
    value: Any, *, key: str, zero_is_unlimited: bool = False
) -> int | None:
    """A row-count option's value as a number of rows, or None for unlimited.

    A config file can put anything under a key, so this is where a row count is
    made a number, for both commands. `zero_is_unlimited` is for the Results
    Viewer's cap alone, where 0 has always meant "hold everything" and a viewer
    holding no rows would serve nobody.

    Raises: HarlequinConfigError if the value is not a whole number of rows.
    """

    def refuse(detail: str) -> HarlequinConfigError:
        return HarlequinConfigError(
            f"{key}={value!r} is {detail}.",
            title=CONFIG_ERROR_TITLE,
        )

    # a bool is an int in Python, and `limit = true` is not one row
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise refuse("not a whole number of rows")
    try:
        rows = int(value)
    except (TypeError, ValueError):
        raise refuse("not a whole number of rows") from None
    if rows < UNLIMITED:
        raise refuse(f"not a number of rows. Pass {UNLIMITED} for no limit")
    if rows == UNLIMITED or (rows == 0 and zero_is_unlimited):
        return None
    return rows


def discover_config_files(config_path: Path | None) -> list[Path]:
    """Every config file that exists, highest priority first.

    Existence is the whole of what is checked: each path is one a command would
    open, in the order it would open them, and none of them is read -- so a
    `pyproject.toml` with no `[tool.harlequin]` section is here, and a file that
    is not on disk is not.

    Raises: HarlequinConfigError if `config_path` names a file that is not there.
    """
    return list(_discover_config_files(config_path))


def get_highest_priority_existing_config_file() -> Path | None:
    """The nearest config file, skipping a pyproject.toml with no section of ours."""
    for path in _discover_config_files(config_path=None):
        if path.stem == "pyproject":
            try:
                config_file = ConfigFile(path)
            except HarlequinConfigError:
                continue
            if not config_file.relevant_config:
                continue
        return path
    return None


def sluggify_option_name(raw: str) -> str:
    return raw.strip("-").replace("-", "_")


def _discover_config_files(config_path: Path | None) -> Iterator[Path]:
    """Every config file that exists, highest priority first.

    Within a directory too: a `harlequin.toml` outranks the `.harlequin.toml`
    beside it, which outranks a `pyproject.toml`'s `[tool.harlequin]` table.

    Raises: HarlequinConfigError if `config_path` names a file that is not there.
    """
    if config_path is not None:
        if not config_path.exists():
            raise HarlequinConfigError(
                f"Config file could not be found at specified path: {config_path}",
                title=CONFIG_ERROR_TITLE,
            )
        yield config_path
    for directory, filenames in _search_directories():
        for filename in filenames:
            path = directory / filename
            if path.exists():
                yield path


def _search_directories() -> Iterator[tuple[Path, tuple[str, ...]]]:
    """Where a config file may be, nearest first, and what it may be called.

    The seam the test suite patches to keep the config files on a developer's
    own machine out of the tests, which is why it is one function and not three
    call sites: an explicit `--config-path` goes around it and keeps working.
    """
    yield Path.cwd(), ("harlequin.toml", ".harlequin.toml", "pyproject.toml")
    yield (
        user_config_path(appname="harlequin", appauthor=False),
        ("harlequin.toml", ".harlequin.toml", "config.toml"),
    )
    yield Path.home(), ("harlequin.toml", ".harlequin.toml", "pyproject.toml")


def _validate_profile(
    profile: Profile,
    *,
    adapter_options: Callable[[str], Sequence[AbstractOption] | None],
    command_options: Collection[str],
    problems: Problems,
) -> None:
    """One profile through the second pass, under the adapter it would run.

    Which is the one it names, or the default -- the pairing `hsql -P name`
    runs, so a key reported here is a key that invocation would refuse.
    """
    adapter = profile.get("adapter") or DEFAULT_ADAPTER
    if not isinstance(adapter, str):
        problems.add(
            f"Profile names an adapter that is not a name: {adapter!r}.", key="adapter"
        )
        return
    try:
        declared = adapter_options(adapter)
    except HarlequinConfigError as e:
        problems.add(e.msg, key="adapter")
        return
    parse_profile_options(
        profile,
        adapter=adapter,
        adapter_options=declared,
        command_options=command_options,
        problems=problems,
    )


def _validate_keymap(
    name: str, bindings: list[dict[str, Any]], *, problems: Problems
) -> None:
    """One keymap through the check the IDE runs when it next starts."""
    try:
        HarlequinKeyMap.from_config(
            name=name, bindings=cast("list[RawKeyBinding]", bindings)
        )
    except HarlequinConfigError as e:
        problems.add(e.msg)


_TOML_LINE = re.compile(r"\(at line (\d+)")


def _line_in(message: str) -> int | None:
    """The line a TOML parse error points at, where its message points at one."""
    match = _TOML_LINE.search(message)
    return int(match.group(1)) if match else None


def _read_config_files(
    config_path: Path | None, *, problems: Problems | None = None
) -> Iterator[tuple[Path, Config]]:
    """Each discovered config file, nearest first, validated as it is read.

    Paired with the path it was read from, because that is what every message
    about it -- and `--config show`'s provenance -- has to name.

    A generator, so a caller that has what it needs stops here: a file it never
    reaches is never opened, parsed or validated. A file with no section of ours
    in it is not yielded at all: it was read, but it defines nothing. With a
    `Problems`, neither is a file that could not be read -- it is an entry in
    there, and the walk goes on to the next file.
    """
    for path in _discover_config_files(config_path):
        raw = ConfigFile(path, problems=problems).relevant_config
        if not raw:
            continue
        config = _parse_config(raw, path=path, problems=problems)
        if config is not None:
            yield path, config


_ENV_VAR = re.compile(
    r"""
      \$\$\{                                  # $${ -- a literal ${, escaped
    | \$\{
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)
        (?: :- (?P<default>[^}]*) )?          # ${VAR:-what to use instead}
      \}
    """,
    re.VERBOSE,
)
"""What a profile's strings are read for: `${VAR}` and `${VAR:-default}`.

Nothing else is a substitution. A bare `{`, a lone `$`, and a `${` that neither
closes nor names an environment variable are all left as they were written, so
a password that happens to contain one needs no escaping -- and `$${` is there
for the one that contains the whole spelling.
"""


def _interpolated(
    value: Any, *, path: Path, key: str = "", problems: Problems | None = None
) -> Any:
    """One profile's values, with `${VAR}` resolved from the environment.

    Recursively through tables and arrays, and over values alone: a key is a
    name this program knows, not something a user parameterizes.

    Raises: HarlequinConfigError naming the variable, the key and the file, for
    a variable that is not set and has no default -- unless a `Problems` is
    there to record it instead, in which case the value stays as it was
    written, so that the rest of the profile is still checked.
    """
    if isinstance(value, str):
        return _interpolated_text(value, path=path, key=key, problems=problems)
    if isinstance(value, Mapping):
        return {
            name: _interpolated(
                item, path=path, key=f"{key}.{name}" if key else name, problems=problems
            )
            for name, item in value.items()
        }
    if isinstance(value, list):
        return [
            _interpolated(item, path=path, key=f"{key}[{i}]", problems=problems)
            for i, item in enumerate(value)
        ]
    return value


def _interpolated_text(
    text: str, *, path: Path, key: str, problems: Problems | None = None
) -> str:
    """One string with its environment variables substituted in.

    A variable set to nothing counts as unset, which is what `:-` means to a
    shell -- and it is the reading that keeps an empty `MYPASSWORD` from
    becoming an authentication error three layers from its cause.
    """
    unset: list[str] = []

    def resolve(match: re.Match[str]) -> str:
        name = match.group("name")
        if name is None:
            return "${"
        if value := os.environ.get(name, ""):
            return value
        if (default := match.group("default")) is not None:
            return default
        unset.append(name)
        return match.group(0)

    resolved = _ENV_VAR.sub(resolve, text)
    if not unset:
        return resolved

    message = (
        f"Config file reads the environment variable {unset[0]}, which is not set. "
        f"Set it, or write ${{{unset[0]}:-a default}} to say what to use when it "
        "is not"
    )
    if problems is None:
        raise _refuse(path, f"{message}, at {key}." if key else f"{message}.")
    problems.at(path).add(message, key=key)
    return text


def _parse_config(
    raw: Mapping[str, Any], *, path: Path, problems: Problems | None = None
) -> Config | None:
    """One file's config, or a HarlequinConfigError naming that file.

    None, and an entry in `problems`, where there is one to record it in.
    """

    def refuse(message: str, *, key: str = "") -> None:
        if problems is None:
            raise _refuse(path, f"{message}, at {key}." if key else f"{message}.")
        problems.at(path).add(message, key=key)

    try:
        config = msgspec.convert(raw, Config)
    except msgspec.ValidationError as e:
        message, key = _in_toml_words(e)
        refuse(message, key=key)
        return None

    if "None" in config.profiles:
        # the name a caller passes to mean "none of them", so a profile cannot
        # have it: `harlequin -P None` would be ambiguous
        refuse("Config file defines a profile named 'None', which is not allowed")
        return None
    return config


def _merge(
    from_file: Config,
    *,
    into: Config,
    source: Path,
    provenance: Provenance | None = None,
) -> None:
    """Add what a lower-priority file defines and a higher-priority one did not.

    One rule, in one place, and `provenance` records the same names it merges:
    a second pass over the same files to work out where a value came from could
    disagree with the merge that produced it.
    """
    if provenance is not None:
        provenance.files.append(source)
        if from_file.default_profile is not None:
            provenance.default_profile.append(source)
        for name in from_file.profiles:
            provenance.profiles.setdefault(name, []).append(source)
        for name in from_file.keymaps:
            provenance.keymaps.setdefault(name, []).append(source)

    if into.default_profile is None:
        into.default_profile = from_file.default_profile
    for profile_name, profile in from_file.profiles.items():
        into.profiles.setdefault(profile_name, profile)
    for keymap_name, bindings in from_file.keymaps.items():
        into.keymaps.setdefault(keymap_name, bindings)


_MISSING = object()
"""What a key not in a TOML table reads as; TOML has no null to collide with."""


def _merge_into_doc(
    target: MutableMapping[str, Any],
    source: Mapping[str, Any],
    *,
    prune: bool = False,
) -> None:
    """Write `source` into a tomlkit table, leaving every node it can in place.

    A node carries the comments and formatting around it, so an unchanged value
    is not written at all, and a table on both sides is merged into rather than
    assigned. `prune` makes `source` the whole table: a key it does not have is
    a key the caller deleted, which is always true of a table the caller named
    and only true at the top level when it passed the whole section.
    """
    for key, value in source.items():
        existing = target.get(key, _MISSING)
        if isinstance(value, Mapping) and isinstance(existing, MutableMapping):
            _merge_into_doc(existing, value, prune=True)
        elif existing is _MISSING or _plain(existing) != value:
            target[key] = value

    if prune:
        for key in [key for key in target if key not in source]:
            del target[key]


def _plain(value: Any) -> Any:
    """A tomlkit node's value, without the styling wrapped around it, so that an
    unchanged value compares equal to the plain data `relevant_config` hands out."""
    unwrap = getattr(value, "unwrap", None)
    return unwrap() if unwrap is not None else value


def _select_profile(
    config: Config,
    *,
    requested: str | None,
    provenance: Provenance | None = None,
    problems: Problems | None = None,
) -> Profile:
    """The profile a name resolves to, once every file has had its say.

    A `default_profile` that names nothing is only an error for an invocation
    that was going to use it: `-P other` has overridden the key. It is the one
    problem no single file has, so it is also the one a `Problems` collects
    here rather than in the pass over a file.

    Pass the merge's `Provenance` to get the profile ready to run: its `${VAR}`s
    resolved from the environment, and the file each one is written in on hand
    to name if the environment does not set it. Resolving them here, where a
    profile is chosen, rather than where its file is read, is what keeps an
    invocation from being refused over a variable named in a profile it is not
    running -- the rule `default_profile` follows one paragraph up.
    """
    name = requested or config.default_profile
    if name is None or name == "None":
        return {}
    if (profile := config.profiles.get(name, None)) is not None:
        source = provenance.profiles.get(name) if provenance is not None else None
        if source is None:
            # a caller that did not ask, or a profile that came out of no file
            # this merge read: nothing written by anyone, and no file to name
            return profile
        return cast(
            Profile, _interpolated(profile, path=source[0], key=f"profiles.{name}")
        )
    if requested is not None:
        # a name typed at the command line rather than written in a file, so
        # there is nowhere to record it and nobody to read it there
        raise HarlequinConfigError(
            f"Could not load the profile named {name} because it does not exist in "
            "any discovered config files.",
            title="Harlequin couldn't load your profile.",
        )
    message = (
        f"Config files set the default_profile to {name}, but no config file defines "
        "a profile with that name."
    )
    if problems is None:
        raise HarlequinConfigError(message, title=CONFIG_ERROR_TITLE)
    problems.add(message, key="default_profile")
    return {}


def _declared_type(option: AbstractOption) -> Any:
    """What an option says its values are, as a type msgspec can parse into.

    A profile writes what TOML makes natural, so `_as_declared()` rounds a
    value toward its option before this model sees it.
    """
    from harlequin.options import FlagOption, ListOption

    if isinstance(option, FlagOption):
        return bool
    if isinstance(option, ListOption):
        return List[str]
    if choices := _declared_choices(option):
        return Literal[tuple(choices)]
    return str


def _as_declared(value: Any, option: AbstractOption | None) -> Any:
    """A value written the way TOML invites, as its option declares it.

    Two roundings, both of which click already makes for a value typed on the
    command line: a number where text was declared is text (`port = 5432`), and
    a choice matches whatever its case (`mode = "RO"`).
    """
    if option is None:
        return value
    if isinstance(value, str):
        for choice in _declared_choices(option) or []:
            if choice.casefold() == value.casefold():
                return choice
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return str(value) if _declared_type(option) is str else value


def _declared_choices(option: AbstractOption) -> list[str] | None:
    """A SelectOption's choices, by duck typing: `choices` may hold pairs."""
    raw = getattr(option, "choices", None)
    if not raw:
        return None
    return [c if isinstance(c, str) else c[0] for c in raw]


def _refuse(path: Path, message: str) -> HarlequinConfigError:
    """One problem, and the file it is written in."""
    return HarlequinConfigError(
        f"{message}\nFound in the config file at {path}.", title=CONFIG_ERROR_TITLE
    )


def _option_problems(
    error: msgspec.ValidationError,
    *,
    adapter: str,
    declared: Mapping[str, AbstractOption],
    given: Mapping[str, Any],
    allowed: Collection[str],
) -> list[tuple[str, str]]:
    """`(key, message)` per option this adapter does not take.

    Every unknown key rather than the one msgspec stopped at: they are all in
    hand here, and a profile with three typos in it is a profile whose author
    would rather learn about three typos than about one, twice.
    """
    if unknown := sorted(set(given) - set(declared)):
        # suggestions come from either set: `read-only` is a near miss for an
        # adapter's option, and `keymap_names` for one of the command's own
        near = [*declared, *allowed]
        return [
            (
                key,
                f"Profile defines an option {key!r}, which is not an option of the "
                f"{adapter} adapter." + _suggestion(key, near),
            )
            for key in unknown
        ]
    message, key = _in_toml_words(error)
    choices = _declared_choices(declared[key]) if key in declared else None
    return [
        (
            key,
            f"Profile sets {key} to a value the {adapter} adapter cannot take: "
            f"{message}."
            + (f"\nAllowed values are {tuple(choices)}." if choices else ""),
        )
    ]


def _suggestion(key: str, among: Sequence[str]) -> str:
    """The nearest spelling to `key`, as a sentence, or nothing like it."""
    match = get_close_matches(key, among, n=1)
    return f" Did you mean {match[0]!r}?" if match else ""


_TOML_WORDS = {
    "object": "table",
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "null": "nothing",
}
"""msgspec names JSON's types; a config file is written in TOML's."""


def _in_toml_words(error: msgspec.ValidationError) -> tuple[str, str]:
    """A msgspec error as `(message, key)`, in the vocabulary of a TOML file.

    Only inside the backticks msgspec puts around a type: everything else in
    the message is the user's own value, and `'internal'` is not two integers.
    """
    message, _, location = str(error).partition(" - at `$")
    message = message.replace(
        "Object contains unknown field", "Found unexpected key in config:"
    )
    spelled = re.sub(
        r"`([^`]*)`",
        lambda match: " ".join(
            _TOML_WORDS.get(word, word) for word in match.group(1).split(" ")
        ),
        message,
    )
    key = location.rstrip("`").replace("[...]", "").lstrip(".")
    return spelled, key
