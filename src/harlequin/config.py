from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Union

from harlequin.exception import HarlequinConfigError

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib

CONFIG_FILENAMES = ("pyproject.toml", ".harlequin.toml")  # order matters!
SEARCH_DIRS = (Path.home(), Path.cwd())

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


def _find_config_files(config_path: Path | None) -> list[Path]:
    found_files: list[Path] = []
    if config_path is None:
        for filename in CONFIG_FILENAMES:
            for p in [p / filename for p in SEARCH_DIRS]:
                if p.exists():
                    found_files.append(p)
    elif config_path.exists():
        found_files.append(config_path)
    else:
        raise HarlequinConfigError(
            f"Config file could not be found at specified path: {config_path}",
            title="Harlequin couldn't load your config file.",
        )
    return found_files


def _merge_config_files(paths: list[Path]) -> Config:
    config: Config = {}
    for p in paths:
        try:
            with open(p, "rb") as f:
                raw_config = tomllib.load(f)
        except OSError as e:
            raise HarlequinConfigError(
                f"Error opening config file at {p}. {e}",
                title="Harlequin couldn't load your config file.",
            ) from e
        except tomllib.TOMLDecodeError as e:
            raise HarlequinConfigError(
                f"Error decoding config file at {p}. " f"Check for invalid TOML. {e}",
                title="Harlequin couldn't load your config file.",
            ) from e
        relevant_config = (
            raw_config
            if p.stem != "pyproject"
            else raw_config.get("tool", {}).get("harlequin", {})
        )
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
                        f"""'{option_name.strip("-").replace("-", "_")}'?""",
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
            f"Config file sets default_profile to {default}, but does not define a "
            "profile with that name.",
            title="Harlequin couldn't load your config file.",
        )
