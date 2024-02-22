from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Union

from platformdirs import user_config_path

from harlequin.exception import HarlequinConfigError

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib


Profile = Dict[str, Union[bool, int, List[str], str, Path]]
Config = Dict[str, Union[str, Dict[str, Profile]]]


def get_config_for_profile(config_path: Path | None, profile: str | None) -> Profile:
    config = load_config(config_path)
    active_profile = profile or config.get("default_profile", None)
    if active_profile is None or active_profile == "None":
        return {}
    elif active_profile not in config.get("profiles", {}):
        raise HarlequinConfigError(
            f"Could not load the profile named {active_profile} because it does not "
            "exist in any discovered config files.",
            title="Harlequin couldn't load your profile.",
        )
    else:
        return config["profiles"][active_profile]  # type: ignore


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
                config = _read_config_file(p)
            except HarlequinConfigError:
                continue
            if not config:
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


def _read_config_file(path: Path) -> Config:
    """
    Reads the relevant config section from a dedicated config file
    or pyproject.toml file at path. Raises HarlequinConfigError
    if there is a problem with the file.
    """
    try:
        with open(path, "rb") as f:
            raw_config = tomllib.load(f)
    except OSError as e:
        raise HarlequinConfigError(
            f"Error opening config file at {path}. {e}",
            title="Harlequin couldn't load your config file.",
        ) from e
    except tomllib.TOMLDecodeError as e:
        raise HarlequinConfigError(
            f"Error decoding config file at {path}. " f"Check for invalid TOML. {e}",
            title="Harlequin couldn't load your config file.",
        ) from e
    relevant_config: Config = (
        raw_config
        if path.stem != "pyproject"
        else raw_config.get("tool", {}).get("harlequin", {})
    )
    return relevant_config


def _merge_config_files(paths: list[Path]) -> Config:
    config: Config = {}
    for p in paths:
        relevant_config = _read_config_file(p)
        config.update(relevant_config)
    return config


def _raise_on_bad_schema(config: Config) -> None:
    TOP_LEVEL_KEYS = ("default_profile", "profiles")
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
                        f"Profile {profile_name} defines an option '{option_name}',"
                        "which is an invalid name for an option. Did you mean "
                        f"""'{sluggify_option_name(option_name)}'?""",
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
