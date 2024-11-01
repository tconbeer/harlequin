from __future__ import annotations

from pathlib import Path
from typing import Sequence, TypedDict, cast

from platformdirs import user_config_path
from tomlkit.exceptions import TOMLKitError
from tomlkit.toml_document import TOMLDocument
from tomlkit.toml_file import TOMLFile

from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyMap, RawKeyBinding


class Profile(TypedDict, total=False):
    conn_str: Sequence[str] | str
    adapter: str
    limit: str | int
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
    def __init__(self, path: Path) -> None:
        """
        Opens and reads the TOML file at path. Can be used to create
        a new file if one does not already exist.

        Stores references to the TOMLFile, TOMLDocument, and tracks
        whether or not the file is a pyproject.toml file.

        Raises: HarlequinConfigError if we can't read the TOML file.
        """
        self.path = path
        self.toml_file = TOMLFile(path)
        try:
            self.toml_doc = self.toml_file.read()
        except OSError:
            self.toml_doc = TOMLDocument()
        except TOMLKitError as e:
            raise HarlequinConfigError(
                f"Attempted to load the config file at {path}, but encountered an "
                f"error:\n\n{e}",
                title="Harlequin could not load the config file.",
            ) from e
        self.is_pyproject = path.stem == "pyproject"

    @property
    def relevant_config(self) -> Config:
        """
        Reads the relevant config section from a dedicated config file
        or pyproject.toml file at path. Raises HarlequinConfigError
        if there is a problem with the file.
        """
        relevant_config: Config = cast(
            Config,
            self.toml_doc.unwrap()
            if not self.is_pyproject
            else self.toml_doc.unwrap().get("tool", {}).get("harlequin", {}),
        )
        return relevant_config

    def update(self, config: Config) -> None:
        """
        Replace the relevant section of the in-memory TOML doc with the updated
        Config.
        """
        if self.is_pyproject:
            if "tool" not in self.toml_doc:
                self.toml_doc["tool"] = {"harlequin": {}}
            elif "harlequin" not in self.toml_doc["tool"]:  # type: ignore
                self.toml_doc["tool"]["harlequin"] = {}  # type: ignore
            self.toml_doc["tool"]["harlequin"].update(config)  # type: ignore
        else:
            self.toml_doc.update(config)

    def write(self) -> None:
        """
        Write the in-memory TOML doc to disk, at self.path.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.toml_file.write(self.toml_doc)


def get_config_for_profile(
    config_path: Path | None, profile_name: str | None
) -> tuple[Profile, list[HarlequinKeyMap]]:
    config = load_config(config_path)
    if not profile_name:
        profile_name = config.get("default_profile", None)

    if profile_name is None or profile_name == "None":
        profile: Profile = {}
    elif profile_name not in config.get("profiles", {}):
        raise HarlequinConfigError(
            f"Could not load the profile named {profile_name} because it does not "
            "exist in any discovered config files.",
            title="Harlequin couldn't load your profile.",
        )
    else:
        profile = config["profiles"][profile_name]

    raw_keymaps: dict[str, list[RawKeyBinding]] = config.get("keymaps", {})
    keymaps: list[HarlequinKeyMap] = [
        HarlequinKeyMap.from_config(name=name, bindings=bindings)
        for name, bindings in raw_keymaps.items()
    ]

    return profile, keymaps


def load_config(config_path: Path | None) -> Config:
    paths = _find_config_files(config_path)
    config = _merge_config_files(paths)
    _raise_on_bad_schema(config)
    return config


def get_highest_priority_existing_config_file() -> Path | None:
    """
    Returns the closest existing config file using the default search path;
    checks pyproject files for a tool.harlequin section and ignores those
    that are missing that section. Returns None if no
    config files are found.
    """
    candidates = _find_config_files(config_path=None)
    while candidates:
        p = candidates.pop()
        if p.stem == "pyproject":
            try:
                config_file = ConfigFile(p)
            except HarlequinConfigError:
                continue
            if not config_file.relevant_config:
                continue
        return p
    return None


def sluggify_option_name(raw: str) -> str:
    return raw.strip("-").replace("-", "_")


def _find_config_files(config_path: Path | None) -> list[Path]:
    """
    Returns a list of candidate config file paths, to be read and
    merged. Returns an empty list if none already exist. Order matters:
    the last item will have highest priority.
    """
    found_files: list[Path] = []
    for search in [_search_home, _search_config, _search_cwd]:
        found_files.extend(search())
    if config_path is not None and config_path.exists():
        found_files.append(config_path)
    elif config_path is not None:
        raise HarlequinConfigError(
            f"Config file could not be found at specified path: {config_path}",
            title="Harlequin couldn't load your config file.",
        )
    return found_files


def _search_cwd() -> list[Path]:
    directory = Path.cwd()
    filenames = ["pyproject.toml", ".harlequin.toml", "harlequin.toml"]
    return [directory / f for f in filenames if (directory / f).exists()]


def _search_config() -> list[Path]:
    directory = user_config_path(appname="harlequin", appauthor=False)
    filenames = ["config.toml", ".harlequin.toml", "harlequin.toml"]
    return [directory / f for f in filenames if (directory / f).exists()]


def _search_home() -> list[Path]:
    directory = Path.home()
    filenames = ["pyproject.toml", ".harlequin.toml", "harlequin.toml"]
    return [directory / f for f in filenames if (directory / f).exists()]


def _merge_config_files(paths: list[Path]) -> Config:
    config: Config = {}
    for p in paths:
        config_file = ConfigFile(p)
        config.update(config_file.relevant_config)
    return config


def _raise_on_bad_schema(config: Config) -> None:
    TOP_LEVEL_KEYS = ("default_profile", "profiles", "keymaps")
    if not config:
        return

    for k in config.keys():
        if k not in TOP_LEVEL_KEYS:
            raise HarlequinConfigError(
                f"Found unexpected key in config: {k}.\n"
                f"Allowed values are {TOP_LEVEL_KEYS}.",
                title="Harlequin couldn't load your config file.",
            )
    if config.get("profiles", None) is None:
        pass
    elif not isinstance(config["profiles"], dict):
        raise HarlequinConfigError(
            "The profiles key must define a table.",
            title="Harlequin couldn't load your config file.",
        )
    elif not all(
        [isinstance(config["profiles"][k], dict) for k in config["profiles"].keys()]
    ):
        raise HarlequinConfigError(
            "The members of the profiles table must be tables.",
            title="Harlequin couldn't load your config file.",
        )
    elif any(k == "None" for k in config["profiles"].keys()):
        raise HarlequinConfigError(
            "Config file defines a profile named 'None', which is not allowed.",
            title="Harlequin couldn't load your config file.",
        )
    else:
        for profile_name, opt_dict in config["profiles"].items():
            for option_name in opt_dict.keys():
                if "-" in option_name:
                    raise HarlequinConfigError(
                        f"Profile {profile_name} defines an option {option_name!r}, "
                        "which is an invalid name for an option. Did you mean "
                        f"{sluggify_option_name(option_name)!r}?",
                        title="Harlequin couldn't load your config file.",
                    )
                elif "keymap_names" in option_name:
                    raise HarlequinConfigError(
                        f"Profile {profile_name} defines an option {option_name!r}, "
                        "which is an invalid name for an option. Did you mean "
                        "'keymap_name' (singular)?",
                        title="Harlequin couldn't load your config file.",
                    )

    if config.get("keymaps", None) is None:
        pass
    elif not isinstance(config["keymaps"], dict):
        raise HarlequinConfigError(
            "The keymaps key must define a table.",
            title="Harlequin couldn't load your config file.",
        )
    elif not all(
        [isinstance(config["keymaps"][k], list) for k in config["keymaps"].keys()]
    ):
        raise HarlequinConfigError(
            "The members of each keymaps table must be arrays of tables.",
            title="Harlequin couldn't load your config file.",
        )

    if (default := config.get("default_profile", None)) is not None and not isinstance(
        default, str
    ):
        raise HarlequinConfigError(
            f"Config file sets default_profile to {default}, but that value "
            "must be a string.",
            title="Harlequin couldn't load your config file.",
        )
    elif (
        default is not None
        and isinstance(default, str)
        and isinstance(config["profiles"], dict)
        and default != "None"
        and config["profiles"].get(default, None) is None
    ):
        raise HarlequinConfigError(
            f"Config files set the default_profile to {default}, but do not define a "
            "profile with that name.",
            title="Harlequin couldn't load your config file.",
        )
