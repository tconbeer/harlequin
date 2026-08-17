"""Reading the TOML files both commands take their options from.

Config files are discovered nearest first -- an explicit `--config-path`, then
the working directory, the user config dir, and home -- and the first file to
define something is the one that defines it.

Who reads what:

| caller                 | function                     | files read           |
| ---------------------- | ---------------------------- | -------------------- |
| `hsql`                 | `load_profile()`             | up to the one that   |
|                        |                              | defines the profile  |
| `harlequin`, `--keys`  | `load_profile_and_keymaps()` | all: keymaps merge   |
|                        |                              | across every file    |
| the IDE's debug screen | `load_config()`              | all                  |
| `harlequin --config`   | `ConfigFile`, at the path    | one, and unvalidated |
|                        | `get_highest_priority_...()` | (it is about to be   |
|                        | returns                      | written back)        |

`load_profile()` is the fast path: it stops at the file that answers the
question, so `hsql -P prod` never opens the files behind that one, and
`hsql -P None` opens none. The others need the whole document.

Config files are validated twice, and the halves know different things.
`_read_config_files()` checks one file's shape before it is merged with any
other, so the error can name the file. `parse_profile_options()` runs once the
adapter is known and checks the profile's remaining keys -- each of which is
some adapter's option -- against the options that adapter declares.
"""

from __future__ import annotations

import re
import sys
from difflib import get_close_matches
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Collection,
    Container,
    Dict,
    Iterator,
    List,
    Literal,
    Mapping,
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
            key: value for key, value in msgspec.structs.asdict(self).items() if value
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

    def __init__(self, path: Path) -> None:
        """
        Opens and reads the TOML file at path. Can be used to create
        a new file if one does not already exist.

        Raises: HarlequinConfigError if we can't read the TOML file.
        """
        self.path = path
        self.is_pyproject = path.stem == "pyproject"
        self._doc: TOMLDocument | None = None
        try:
            with open(path, "rb") as f:
                self._data: dict[str, Any] = tomllib.load(f)
        except OSError:
            self._data = {}
        except tomllib.TOMLDecodeError as e:
            raise HarlequinConfigError(
                f"Attempted to load the config file at {path}, but encountered an "
                f"error:\n\n{e}",
                title="Harlequin could not load the config file.",
            ) from e

    @property
    def relevant_config(self) -> dict[str, Any]:
        """This file's Harlequin section, exactly as written.

        Unvalidated, and the write path depends on that: `harlequin --config`
        reads this, edits it, and writes it back, so anything transformed on the
        way in would be written into the user's file on the way out.
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

    def update(self, config: Mapping[str, Any]) -> None:
        """
        Replace the relevant section of the in-memory TOML doc with the updated
        Config.
        """
        doc = self._editable()
        if self.is_pyproject:
            if "tool" not in doc:
                doc["tool"] = {"harlequin": {}}
            elif "harlequin" not in doc["tool"]:
                doc["tool"]["harlequin"] = {}
            doc["tool"]["harlequin"].update(config)
        else:
            doc.update(config)

    def write(self) -> None:
        """
        Write the in-memory TOML doc to disk, at self.path.
        """
        from tomlkit.toml_file import TOMLFile

        self.path.parent.mkdir(parents=True, exist_ok=True)
        TOMLFile(self.path).write(self._editable())


def load_config(config_path: Path | None) -> Config:
    """Every discovered config file, merged nearest first."""
    config = Config()
    for from_file in _read_config_files(config_path):
        _merge(from_file, into=config)
    return config


def load_profile(config_path: Path | None, profile_name: str | None) -> Profile:
    """The profile an invocation runs under, reading no further than it must."""
    if profile_name == "None":
        return {}  # Harlequin's own defaults, which no config file can change

    config = Config()
    for from_file in _read_config_files(config_path):
        _merge(from_file, into=config)
        name = profile_name or config.default_profile
        if name is not None and name in config.profiles:
            return config.profiles[name]  # the files behind this one go unread
    return _select_profile(config, requested=profile_name)


def load_profile_and_keymaps(
    config_path: Path | None, profile_name: str | None
) -> tuple[Profile, list[HarlequinKeyMap]]:
    """One profile, and every keymap, for the IDE, which needs both."""
    config = load_config(config_path)
    # a binding is a `RawKeyBinding` once `from_config()` has said so; the shape
    # of the table around it is all `Config` promises
    keymaps = [
        HarlequinKeyMap.from_config(
            name=keymap_name, bindings=cast("list[RawKeyBinding]", bindings)
        )
        for keymap_name, bindings in config.keymaps.items()
    ]
    return _select_profile(config, requested=profile_name), keymaps


def parse_profile_options(
    profile: Profile,
    *,
    adapter: str,
    adapter_options: Sequence[AbstractOption] | None,
    command_options: Collection[str],
) -> Profile:
    """The profile, with its adapter's options parsed as that adapter declares them.

    `command_options` names the keys a command reads for itself; every other
    key has to be an option of `adapter`, and this is the only place that can
    tell, since an adapter's constructor takes supersets of what it declares
    and drops the rest in silence.

    Raises: HarlequinConfigError, naming the option and the adapter.
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
            given, _adapter_options_model(adapter, declared), strict=False
        )
    except msgspec.ValidationError as e:
        raise _refuse_option(
            e, adapter=adapter, declared=declared, given=given, allowed=command_options
        ) from e

    return {**profile, **{key: getattr(parsed, key) for key in given}}


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


def _read_config_files(config_path: Path | None) -> Iterator[Config]:
    """Each discovered config file, nearest first, validated as it is read.

    A generator, so a caller that has what it needs stops here: a file it never
    reaches is never opened, parsed or validated.
    """
    for path in _discover_config_files(config_path):
        raw = ConfigFile(path).relevant_config
        if raw:
            yield _parse_config(raw, path=path)


def _parse_config(raw: Mapping[str, Any], *, path: Path) -> Config:
    """One file's config, or a HarlequinConfigError naming that file."""
    try:
        config = msgspec.convert(raw, Config)
    except msgspec.ValidationError as e:
        message, key = _in_toml_words(e)
        raise _refuse(path, f"{message}, at {key}." if key else f"{message}.") from e

    if "None" in config.profiles:
        # the name a caller passes to mean "none of them", so a profile cannot
        # have it: `harlequin -P None` would be ambiguous
        raise _refuse(
            path, "Config file defines a profile named 'None', which is not allowed."
        )
    return config


def _merge(from_file: Config, *, into: Config) -> None:
    """Add what a lower-priority file defines and a higher-priority one did not."""
    if into.default_profile is None:
        into.default_profile = from_file.default_profile
    for profile_name, profile in from_file.profiles.items():
        into.profiles.setdefault(profile_name, profile)
    for keymap_name, bindings in from_file.keymaps.items():
        into.keymaps.setdefault(keymap_name, bindings)


def _select_profile(config: Config, *, requested: str | None) -> Profile:
    """The profile a name resolves to, once every file has had its say.

    A `default_profile` that names nothing is only an error for an invocation
    that was going to use it: `-P other` has overridden the key.
    """
    name = requested or config.default_profile
    if name is None or name == "None":
        return {}
    if (profile := config.profiles.get(name, None)) is not None:
        return profile
    if requested is not None:
        raise HarlequinConfigError(
            f"Could not load the profile named {name} because it does not exist in "
            "any discovered config files.",
            title="Harlequin couldn't load your profile.",
        )
    raise HarlequinConfigError(
        f"Config files set the default_profile to {name}, but no config file defines "
        "a profile with that name.",
        title=CONFIG_ERROR_TITLE,
    )


def _adapter_options_model(
    adapter: str, declared: Mapping[str, AbstractOption]
) -> type[msgspec.Struct]:
    """One adapter's declared options, as a model a profile can be parsed into."""
    fields: list[tuple[str, type, Any]] = [
        (name, Optional[_declared_type(option)], None)  # type: ignore[misc]
        for name, option in declared.items()
        if name.isidentifier()
    ]
    return msgspec.defstruct(f"{adapter}_options", fields, forbid_unknown_fields=True)


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


def _refuse_option(
    error: msgspec.ValidationError,
    *,
    adapter: str,
    declared: Mapping[str, AbstractOption],
    given: Mapping[str, Any],
    allowed: Collection[str],
) -> HarlequinConfigError:
    """An option this adapter does not declare, or a value it cannot take."""
    if unknown := sorted(set(given) - set(declared)):
        # from either set: `read-only` is a near miss for an adapter's option,
        # and `keymap_names` for one of the command's own
        suggestion = get_close_matches(unknown[0], [*declared, *allowed], n=1)
        return HarlequinConfigError(
            f"Profile defines an option {unknown[0]!r}, which is not an option of the "
            f"{adapter} adapter."
            + (f" Did you mean {suggestion[0]!r}?" if suggestion else ""),
            title=CONFIG_ERROR_TITLE,
        )
    message, key = _in_toml_words(error)
    choices = _declared_choices(declared[key]) if key in declared else None
    return HarlequinConfigError(
        f"Profile sets {key} to a value the {adapter} adapter cannot take: {message}."
        + (f"\nAllowed values are {tuple(choices)}." if choices else ""),
        title=CONFIG_ERROR_TITLE,
    )


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
