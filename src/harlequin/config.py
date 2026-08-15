"""Reading, validating and merging the TOML files both commands run from.

Files are read **nearest first** -- an explicit `--config-path`, then the
working directory, the user config dir, and the home directory -- and the first
file to define something is the one that defines it. Reading in priority order
is what lets a caller that wants one profile stop as soon as it has it: the
files further out are never opened, parsed or validated.

Validation runs in two passes. The first is per file, ahead of any merge, so an
error can name the file the key came from; it covers what core owns -- the
top-level keys, the shape of `profiles` and `keymaps`, and the profile keys that
mean something to `harlequin` or `hsql`. The second (`validate_profile_options`)
runs once the adapter is known and checks the profile's remaining keys against
the options that adapter declares, which is the only place in the stack that can
tell `reed_only` from `read_only`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Container,
    Iterator,
    Literal,
    Mapping,
    Optional,
    Sequence,
    TypedDict,
    cast,
)

from platformdirs import user_config_path

from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyMap, RawKeyBinding

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

if TYPE_CHECKING:
    import msgspec
    from tomlkit.toml_document import TOMLDocument

    from harlequin.options import AbstractOption

DEFAULT_ADAPTER = "duckdb"
"""The adapter both commands connect with when nothing names one."""

CONFIG_ERROR_TITLE = "Harlequin couldn't load your config file."
"""The title every config error carries, so a caller can match on one string."""

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

TOP_LEVEL_KEYS = ("default_profile", "profiles", "keymaps")
"""Everything a config file may define at its top level."""

CORE_PROFILE_KEYS = frozenset(
    {
        # both commands
        "adapter",
        "conn_str",
        "limit",
        # hsql
        "color",
        "command",
        "csv",
        "display_rows",
        "file",
        "format",
        "json",
        "jsonl",
        "markdown",
        "no_align",
        "no_footer",
        "no_header",
        "null_string",
        "on_error",
        "output",
        "result",
        "stats",
        "tuples_only",
        "vertical",
    }
)
"""Profile keys a command reads for itself, rather than handing to an adapter.

Together with `TUI_ONLY_KEYS` and whatever the adapter declares, this is the set
a profile may draw from -- so these names are reserved: an adapter that declared
an option called `limit` would find a command had already taken the value.
"""


class Profile(TypedDict, total=False):
    conn_str: Sequence[str] | str
    adapter: str
    limit: str | int
    viewer_max_rows: str | int
    display_rows: str | int
    theme: str
    keymap_name: list[str]
    show_files: Path | str | None
    show_s3: str | None
    locale: str
    no_download_tzdata: bool
    # many more keys for adapter options


class Config(TypedDict, total=False):
    default_profile: str | None
    keymaps: dict[str, list[RawKeyBinding]]
    profiles: dict[str, Profile]


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
    def relevant_config(self) -> Config:
        """
        Reads the relevant config section from a dedicated config file
        or pyproject.toml file at path. Raises HarlequinConfigError
        if there is a problem with the file.
        """
        relevant_config: Config = cast(
            Config,
            self._data
            if not self.is_pyproject
            else self._data.get("tool", {}).get("harlequin", {}),
        )
        return relevant_config

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

    def update(self, config: Config) -> None:
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


def get_config_for_profile(
    config_path: Path | None, profile_name: str | None
) -> tuple[Profile, list[HarlequinKeyMap]]:
    """One profile and every keymap, for a caller that needs both.

    Keymaps come from every file, so this reads them all. A caller that only
    wants the profile should call `get_profile()`, which stops at the file that
    defines it.
    """
    config = load_config(config_path)
    profile = _select_profile(
        config.get("profiles", {}),
        requested=profile_name,
        default=config.get("default_profile", None),
    )

    raw_keymaps: dict[str, list[RawKeyBinding]] = config.get("keymaps", {})
    keymaps: list[HarlequinKeyMap] = [
        HarlequinKeyMap.from_config(name=name, bindings=bindings)
        for name, bindings in raw_keymaps.items()
    ]

    return profile, keymaps


def get_profile(config_path: Path | None, profile_name: str | None) -> Profile:
    """The one profile an invocation runs under, reading no more than it must.

    Files are read nearest first, so the first one to define the wanted profile
    is the one that wins -- and the files behind it are never opened. A profile
    the caller did not name is only wanted once some file says it is the
    default, so the files read before that one are held until it does.
    """
    if profile_name == "None":
        return {}

    wanted = profile_name
    names_default: Path | None = None
    held: list[_ReadFile] = []
    for file in _read_config_files(config_path):
        if wanted is None:
            if file.default_profile is None:
                held.append(file)
                continue
            wanted = file.default_profile
            if wanted == "None":
                return {}
            names_default = file.path
            for nearer in held:
                if wanted in nearer.profiles:
                    return nearer.profiles[wanted]
        if wanted in file.profiles:
            return file.profiles[wanted]

    if wanted is None:
        # nothing named a default, which is not an error: it is what a config
        # file that only defines keymaps, or no config file at all, looks like
        return {}
    raise _no_such_profile(wanted, path=names_default)


def validate_profile_options(
    profile: Profile,
    *,
    adapter: str,
    options: Sequence[AbstractOption] | None,
) -> None:
    """Check a profile's keys against the options its adapter declares.

    The second pass, and the only place that knows both halves: core cannot
    reject a key it does not recognize, because every adapter's options live in
    the same table, and an adapter takes supersets of what it declares -- so
    `reed_only = true` is otherwise dropped in silence by the constructor, and
    the connection a user believed was read-only is not.

    Names and declared choices, not types: the adapter contract says values may
    arrive uncast, so `port = 5432` and `port = "5432"` both have to pass.
    """
    declared = {sluggify_option_name(o.name): o for o in options or []}
    allowed = CORE_PROFILE_KEYS | frozenset(TUI_ONLY_KEYS) | declared.keys()

    unknown = [key for key in profile if key not in allowed]
    if unknown:
        from difflib import get_close_matches

        suggestion = get_close_matches(unknown[0], allowed, n=1)
        raise HarlequinConfigError(
            f"Profile defines an option {unknown[0]!r}, which is not an option of "
            f"the {adapter} adapter."
            + (f" Did you mean {suggestion[0]!r}?" if suggestion else ""),
            title=CONFIG_ERROR_TITLE,
        )

    if not any(_declared_choices(option) for option in declared.values()):
        # nothing is left to check: values may arrive uncast, so a declared set
        # of choices is the only thing that constrains one
        return

    import msgspec

    try:
        msgspec.convert(
            _as_declared(profile, declared), _adapter_profile_model(adapter, declared)
        )
    except msgspec.ValidationError as e:
        message, key = _describe(e)
        option = declared.get(key, None)
        choices = _declared_choices(option) if option is not None else None
        raise HarlequinConfigError(
            f"Profile sets {key} to a value the {adapter} adapter does not take: "
            f"{message}."
            + (f"\nAllowed values are {tuple(choices)}." if choices else ""),
            title=CONFIG_ERROR_TITLE,
        ) from e


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
    return cast(Profile, merged)


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


def load_config(config_path: Path | None) -> Config:
    """Every discovered config file, merged nearest first.

    A file merges into the ones behind it one key deeper than it used to: a
    `profiles` table no longer replaces another file's outright, so a
    project-local file that defines one profile leaves the rest of them --
    and the `default_profile` that names one of them -- alone.
    """
    config: Config = {}
    profiles: dict[str, Profile] = {}
    keymaps: dict[str, list[RawKeyBinding]] = {}
    names_default: Path | None = None
    for file in _read_config_files(config_path):
        if file.default_profile is not None and "default_profile" not in config:
            config["default_profile"] = file.default_profile
            names_default = file.path
        for profile_name, profile in file.profiles.items():
            profiles.setdefault(profile_name, profile)
        for keymap_name, bindings in file.keymaps.items():
            keymaps.setdefault(keymap_name, bindings)

    if profiles:
        config["profiles"] = profiles
    if keymaps:
        config["keymaps"] = keymaps

    default = config.get("default_profile", None)
    if default is not None and default != "None" and default not in profiles:
        raise _no_such_profile(default, path=names_default)
    return config


def get_highest_priority_existing_config_file() -> Path | None:
    """
    Returns the closest existing config file using the default search path;
    checks pyproject files for a tool.harlequin section and ignores those
    that are missing that section. Returns None if no
    config files are found.
    """
    for path in _find_config_files(config_path=None):
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


@dataclass(frozen=True)
class _ReadFile:
    """One config file's contents, after the keys core owns were checked."""

    path: Path
    default_profile: str | None
    profiles: dict[str, Profile]
    keymaps: dict[str, list[RawKeyBinding]]


def _read_config_files(config_path: Path | None) -> Iterator[_ReadFile]:
    """Each discovered config file, nearest first, validated as it is read.

    A generator because a caller that has what it wants stops here: a file it
    never reached is a file that is never opened, parsed or validated.
    """
    for path in _find_config_files(config_path):
        raw = ConfigFile(path).relevant_config
        if raw:
            yield _validate_file(raw, path=path)


def _find_config_files(config_path: Path | None) -> list[Path]:
    """
    Returns a list of candidate config file paths, to be read and
    merged. Returns an empty list if none already exist. Order matters:
    the first item will have highest priority.
    """
    found_files: list[Path] = []
    if config_path is not None and config_path.exists():
        found_files.append(config_path)
    elif config_path is not None:
        raise HarlequinConfigError(
            f"Config file could not be found at specified path: {config_path}",
            title=CONFIG_ERROR_TITLE,
        )
    for search in [_search_cwd, _search_config, _search_home]:
        found_files.extend(search())
    return found_files


# each search returns its own directory's files in priority order, too: a
# `harlequin.toml` outranks the `.harlequin.toml` beside it, which outranks the
# `[tool.harlequin]` table of a `pyproject.toml`.
def _search_cwd() -> list[Path]:
    directory = Path.cwd()
    filenames = ["harlequin.toml", ".harlequin.toml", "pyproject.toml"]
    return [directory / f for f in filenames if (directory / f).exists()]


def _search_config() -> list[Path]:
    directory = user_config_path(appname="harlequin", appauthor=False)
    filenames = ["harlequin.toml", ".harlequin.toml", "config.toml"]
    return [directory / f for f in filenames if (directory / f).exists()]


def _search_home() -> list[Path]:
    directory = Path.home()
    filenames = ["harlequin.toml", ".harlequin.toml", "pyproject.toml"]
    return [directory / f for f in filenames if (directory / f).exists()]


def _select_profile(
    profiles: Mapping[str, Profile], *, requested: str | None, default: str | None
) -> Profile:
    """The profile a name resolves to, out of an already-merged config."""
    name = requested or default
    if name is None or name == "None":
        return {}
    if name not in profiles:
        # only a requested name can be missing here: `load_config()` has
        # already reported a default that names no profile, and named the file
        raise _no_such_profile(name, path=None)
    return profiles[name]


def _no_such_profile(name: str, *, path: Path | None) -> HarlequinConfigError:
    """`path` is the file that named this profile the default, if one did."""
    if path is None:
        return HarlequinConfigError(
            f"Could not load the profile named {name} because it does not "
            "exist in any discovered config files.",
            title="Harlequin couldn't load your profile.",
        )
    return _bad_config(
        path,
        f"Config files set the default_profile to {name}, but do not define a "
        "profile with that name.",
    )


def _validate_file(raw: Mapping[str, Any], *, path: Path) -> _ReadFile:
    """Everything core owns in one file, checked before it meets another file.

    Per file, and ahead of the merge, so that every message can name the file
    the offending key is actually written in -- the merged document is a
    structure nobody wrote and no message can point at.
    """
    for key in raw:
        if key not in TOP_LEVEL_KEYS:
            raise _bad_config(
                path,
                f"Found unexpected key in config: {key}.\n"
                f"Allowed values are {TOP_LEVEL_KEYS}.",
            )

    _check(raw, _file_model(), path=path)

    profiles = cast("dict[str, Profile]", raw.get("profiles", None) or {})
    for name, profile in profiles.items():
        _validate_profile(profile, name=name, path=path)

    keymaps = cast("dict[str, list[RawKeyBinding]]", raw.get("keymaps", None) or {})
    for name, bindings in keymaps.items():
        _check(bindings, _keymap_model(), path=path, at=f"keymaps.{name}")

    return _ReadFile(
        path=path,
        default_profile=raw.get("default_profile", None),
        profiles=profiles,
        keymaps=keymaps,
    )


def _validate_profile(profile: Mapping[str, Any], *, name: str, path: Path) -> None:
    """One profile's name, its key names, and the types of the keys core owns.

    Not the keys it does not recognize: every adapter's options live in this
    same table, so rejecting an unknown one needs the adapter, which is
    `validate_profile_options`.
    """
    if name == "None":
        raise _bad_config(
            path, "Config file defines a profile named 'None', which is not allowed."
        )
    # the shape first: the loop below would iterate the characters of a string
    _check(profile, _core_profile_model(), path=path, at=f"profiles.{name}")
    for option_name in profile:
        if "-" in option_name:
            raise _bad_config(
                path,
                f"Profile {name} defines an option {option_name!r}, which is an "
                f"invalid name for an option. Did you mean "
                f"{sluggify_option_name(option_name)!r}?",
            )
        elif "keymap_names" in option_name:
            raise _bad_config(
                path,
                f"Profile {name} defines an option {option_name!r}, which is an "
                "invalid name for an option. Did you mean 'keymap_name' (singular)?",
            )


def _check(data: Any, model: Any, *, path: Path, at: str = "") -> None:
    """Structure `data` as `model`, for the error rather than the result.

    The models declare what core owns; the value they build is thrown away,
    because the profile a command runs from is the raw table the user wrote --
    a struct would drop every adapter option it had never heard of.
    """
    import msgspec

    try:
        msgspec.convert(data, model)
    except msgspec.ValidationError as e:
        message, key = _describe(e, at=at)
        raise _bad_config(
            path, f"{message}, at {key}." if key else f"{message}."
        ) from e


_TOML_WORDS = {
    "object": "table",
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}
"""msgspec names JSON's types; a config file is written in TOML's."""


def _describe(error: Exception, *, at: str = "") -> tuple[str, str]:
    """A msgspec validation error, and the key it is about.

    Rendered in the words a TOML file is written in: msgspec names JSON's
    types, and a user looking for `object` in the TOML spec will not find it.
    """
    message, _, location = str(error).partition(" - at `$")
    key = f"{at}{location.rstrip('`')}".lstrip(".")
    for jsonish, tomlish in _TOML_WORDS.items():
        message = re.sub(rf"\b{jsonish}\b", tomlish, message)
    return message.replace("`", ""), key


def _bad_config(path: Path, message: str) -> HarlequinConfigError:
    """One problem, and the file it is written in -- which is the whole point.

    A message from before the merge can name the file; one from after it can
    only name a key of a document no user wrote.
    """
    return HarlequinConfigError(
        f"{message}\nFound in the config file at {path}.",
        title=CONFIG_ERROR_TITLE,
    )


@lru_cache(maxsize=1)
def _file_model() -> type[msgspec.Struct]:
    """What a config file may say, as far as core is concerned.

    Built on first use rather than at import: msgspec costs ~30ms to import,
    and an invocation that finds no config file with anything in it should not
    pay it. `profiles` and `keymaps` stay raw tables -- a struct would drop the
    adapter options and the bindings that are the point of them, and each is
    checked one level down, where an error can name which one.
    """
    import msgspec

    class _ConfigFileModel(msgspec.Struct):
        default_profile: str | None = None
        profiles: dict[str, Any] = msgspec.field(default_factory=dict)
        keymaps: dict[str, Any] = msgspec.field(default_factory=dict)

    return _ConfigFileModel


@lru_cache(maxsize=1)
def _keymap_model() -> Any:
    return list[dict[str, Any]]


@lru_cache(maxsize=1)
def _core_profile_model() -> type[msgspec.Struct]:
    """The profile keys a command reads for itself, and what they may hold.

    Strict, which is the point of declaring them: `limit = true` is rejected
    here rather than quietly meaning one row. Unknown keys are *not* rejected --
    they are the adapter's options, and `validate_profile_options` is what knows
    whether they are real.
    """
    import msgspec

    class _CoreProfileModel(msgspec.Struct):
        adapter: str | None = None
        conn_str: str | list[str] | None = None
        limit: int | str | None = None
        display_rows: int | str | None = None
        viewer_max_rows: int | str | None = None
        result: int | str | None = None
        theme: str | None = None
        keymap_name: str | list[str] | None = None
        locale: str | None = None
        show_files: str | None = None
        show_s3: str | None = None
        no_download_tzdata: bool | None = None
        format: str | None = None
        output: str | None = None
        on_error: str | None = None
        null_string: str | None = None
        color: str | None = None

    return _CoreProfileModel


def _adapter_profile_model(
    adapter: str, declared: Mapping[str, AbstractOption]
) -> type[msgspec.Struct]:
    """One adapter's declared options, as a model to validate a profile with.

    Every option is a field so that the model describes the adapter (which is
    what a JSON Schema of it will want), but only a declared set of choices
    constrains a value: the contract promises values may arrive uncast, so
    nothing here may reject `port = "5432"`.
    """
    import msgspec

    fields: list[tuple[str, Any, Any]] = []
    for key, option in declared.items():
        if not key.isidentifier():
            continue
        choices = _declared_choices(option)
        annotation: Any = Any if choices is None else Optional[Literal[tuple(choices)]]
        fields.append((key, annotation, None))
    return msgspec.defstruct(f"{adapter}_profile", fields)


def _declared_choices(option: AbstractOption) -> list[str] | None:
    """A SelectOption's choices, by duck typing rather than by class.

    `choices` may hold a value or a (value, label) pair, and an option type a
    third party wrote may have the attribute without being a `SelectOption`.
    """
    raw = getattr(option, "choices", None)
    if not raw:
        return None
    return [c if isinstance(c, str) else c[0] for c in raw]


def _as_declared(
    profile: Profile, declared: Mapping[str, AbstractOption]
) -> dict[str, Any]:
    """The profile, with each choice spelled the way its option declares it.

    click matches choices case-insensitively, so a profile that writes
    `sslmode = "VERIFY-FULL"` reaches the adapter today and has to keep doing
    so. The copy is what gets validated; the profile itself is not rewritten.
    """
    values: dict[str, Any] = dict(profile)
    for key, option in declared.items():
        value = values.get(key, None)
        if not isinstance(value, str):
            continue
        choices = _declared_choices(option)
        if not choices or value in choices:
            continue
        for choice in choices:
            if choice.lower() == value.lower():
                values[key] = choice
                break
    return values
