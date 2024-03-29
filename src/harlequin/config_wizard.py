from __future__ import annotations

from pathlib import Path
from typing import Any

import questionary
import tomlkit
from pygments.styles import get_all_styles
from rich import print as rich_print
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from tomlkit.exceptions import TOMLKitError
from tomlkit.toml_document import TOMLDocument
from tomlkit.toml_file import TOMLFile

from harlequin.adapter import HarlequinAdapter
from harlequin.colors import HARLEQUIN_QUESTIONARY_STYLE, YELLOW
from harlequin.config import (
    get_highest_priority_existing_config_file,
    sluggify_option_name,
)
from harlequin.exception import HarlequinWizardError, pretty_print_error
from harlequin.options import ListOption
from harlequin.plugins import load_plugins


def wizard(config_path: Path | None) -> None:
    try:
        _wizard(config_path)
    except KeyboardInterrupt:
        print("Cancelled config updates. No changes were made to any files.")
        return
    except HarlequinWizardError as e:
        pretty_print_error(e)
        return


def _wizard(config_path: Path | None) -> None:
    path, is_pyproject = _prompt_for_path(config_path)
    config, file = _read_toml(path)

    # extract existing profiles from config file.
    if is_pyproject:
        full_config = config
        config = config.get("tool", {}).get("harlequin", {})
    if "profiles" not in config:
        config["profiles"] = {}
    profiles = config.get("profiles", {})

    profile_name = _prompt_for_profile_name(profiles)
    selected_profile = profiles.get(profile_name, {})

    adapters = load_plugins()
    adapter = questionary.select(
        message="Which adapter should this profile use?",
        choices=sorted(adapters.keys()),
        default=selected_profile.get("adapter", "duckdb"),
        style=HARLEQUIN_QUESTIONARY_STYLE,
    ).unsafe_ask()

    conn_str = questionary.text(
        message="What connection string(s) should this profile use?",
        instruction="Separate items by a space.",
        default=" ".join(selected_profile.get("conn_str", [])),
        style=HARLEQUIN_QUESTIONARY_STYLE,
    ).unsafe_ask()

    theme = questionary.select(
        message="What theme should this profile use?",
        choices=sorted(get_all_styles()),
        default=selected_profile.get("theme", "harlequin"),
        style=HARLEQUIN_QUESTIONARY_STYLE,
    ).unsafe_ask()

    limit = int(
        questionary.text(
            message="How many rows should the data table show?",
            validate=_validate_int,
            default=str(selected_profile.get("limit", 100000)),
            style=HARLEQUIN_QUESTIONARY_STYLE,
        ).unsafe_ask()
    )

    show_files = questionary.path(
        message="Show local files from a directory? (Leave blank to hide)",
        validate=_validate_dir_or_blank,
        only_directories=True,
        default=str(selected_profile.get("show_files", "")),
        style=HARLEQUIN_QUESTIONARY_STYLE,
    ).unsafe_ask()

    show_s3 = questionary.text(
        message="Show cloud storage files?",
        instruction=(
            "Enter bucket name or URI (or `all`), or leave blank to hide "
            "cloud storage viewer."
        ),
        default=str(selected_profile.get("show_s3", "")),
        style=HARLEQUIN_QUESTIONARY_STYLE,
    ).unsafe_ask()

    locale = questionary.text(
        message="What locale should Harlequin use for formatting numbers?",
        instruction="Leave blank to use the system locale.",
        default=selected_profile.get("locale", ""),
        style=HARLEQUIN_QUESTIONARY_STYLE,
    ).unsafe_ask()

    adapter_cls = adapters[adapter]
    adapter_option_choices = (
        [
            questionary.Choice(
                title=opt.name,
                checked=sluggify_option_name(opt.name) in selected_profile,
            )
            for opt in adapter_cls.ADAPTER_OPTIONS
        ]
        if adapter_cls.ADAPTER_OPTIONS is not None
        else []
    )

    if adapter_option_choices:
        which = questionary.checkbox(
            message="Which of the following adapter options would you like to set?",
            choices=adapter_option_choices,
            style=HARLEQUIN_QUESTIONARY_STYLE,
        ).unsafe_ask()
    else:
        which = []

    adapter_options = {}
    if conn_str:
        adapter_options["conn_str"] = conn_str.split(" ")
    _prompt_to_set_adapter_options(
        adapter_options=adapter_options,
        adapter_cls=adapter_cls,
        which=which,
        selected_profile=selected_profile,
    )

    default_profile = _prompt_to_set_default_profile(profile_name, config, profiles)

    new_profile = {
        "adapter": adapter,
        "theme": theme,
        "limit": limit,
    }

    if show_files:
        new_profile["show_files"] = show_files

    if show_s3:
        new_profile["show_s3"] = show_s3

    if locale:
        new_profile["locale"] = locale

    new_profile.update(adapter_options)

    _confirm_profile_generation(default_profile, profile_name, new_profile)

    config["profiles"][profile_name] = new_profile  # type: ignore

    if is_pyproject:
        if "tool" not in full_config:
            full_config["tool"] = {}
        full_config["tool"]["harlequin"] = config  # type: ignore
        config = full_config

    path.parent.mkdir(parents=True, exist_ok=True)
    file.write(config)


def _prompt_for_path(config_path: Path | None) -> tuple[Path, bool]:
    if config_path is None:
        existing = get_highest_priority_existing_config_file()
        raw_path: str = questionary.path(
            "What config file do you want to create or update?",
            default=str(existing) if existing is not None else ".harlequin.toml",
            validate=lambda p: (
                True if p.endswith(".toml") else "Must have a .toml extension"
            ),
            style=HARLEQUIN_QUESTIONARY_STYLE,
        ).unsafe_ask()
        path = Path(raw_path).expanduser().resolve()
    else:
        path = config_path
        rich_print(
            f"[italic]Updating the file at [bold {YELLOW}]{escape(str(path))}"
            f"[/ bold {YELLOW}]:[/]"
        )
    if path.suffix != ".toml":
        raise HarlequinWizardError(
            msg="Must create a file with a .toml extension.",
            title="Harlequin could not create your configuration.",
        )
    is_pyproject = path.stem == "pyproject"
    return path, is_pyproject


def _read_toml(path: Path) -> tuple[TOMLDocument, TOMLFile]:
    file = TOMLFile(path)
    try:
        config = file.read()
    except OSError:
        config = TOMLDocument()
    except TOMLKitError as e:
        raise HarlequinWizardError(
            f"Attempted to load the config file at {path}, but encountered an "
            f"error:\n\n{e}",
            title="Harlequin could not load the config file.",
        ) from e
    return config, file


def _prompt_for_profile_name(profiles: TOMLDocument) -> str:
    NEW_PROFILE_SENTINEL = "[Create a New Profile]"
    profile_name = NEW_PROFILE_SENTINEL
    if profiles:
        profile_name = questionary.select(
            message="Which profile would you like to update?",
            choices=[NEW_PROFILE_SENTINEL, *profiles.keys()],
            style=HARLEQUIN_QUESTIONARY_STYLE,
        ).unsafe_ask()
    if profile_name == NEW_PROFILE_SENTINEL:
        profile_name = questionary.text(
            message="What would you like to name your profile?",
            style=HARLEQUIN_QUESTIONARY_STYLE,
            validate=lambda x: True if x and x != "None" else "Cannot be empty or None",
        ).unsafe_ask()
    return profile_name


def _prompt_to_set_adapter_options(
    adapter_options: dict[str, Any],
    adapter_cls: type[HarlequinAdapter],
    which: list[str],
    selected_profile: TOMLDocument,
) -> None:
    """
    Mutates passed adapter_options dict.
    """
    if which and adapter_cls.ADAPTER_OPTIONS is not None:
        for option in adapter_cls.ADAPTER_OPTIONS:
            if option.name not in which:
                continue
            value = option.to_questionary(
                selected_profile.get(sluggify_option_name(option.name), None)
            ).unsafe_ask()
            if isinstance(option, ListOption):
                value = value.split(" ")
            adapter_options.update({sluggify_option_name(option.name): value})


def _prompt_to_set_default_profile(
    profile_name: str, config: TOMLDocument, profiles: TOMLDocument
) -> str | None:
    possible_names = set([profile_name, *profiles.keys()])
    NO_DEFAULT_SENTINEL = "[No default]"
    default_profile: str = questionary.select(
        message="Would you like to set a default profile?",
        choices=[
            NO_DEFAULT_SENTINEL,
            *possible_names,
        ],
        default=config.get("default_profile", None),
        style=HARLEQUIN_QUESTIONARY_STYLE,
    ).unsafe_ask()

    if default_profile == NO_DEFAULT_SENTINEL:
        _ = config.pop("default_profile", None)
        return None
    else:
        config["default_profile"] = default_profile
        return default_profile


def _confirm_profile_generation(
    default_profile: str | None, profile_name: str, new_profile: dict[str, Any]
) -> None:
    new_config: dict[str, Any] = (
        {} if default_profile is None else {"default_profile": default_profile}
    )
    new_config.update({"profiles": {profile_name: new_profile}})
    new_config_toml = tomlkit.dumps(new_config).rstrip()

    rich_print("[italic] We generated the following profile:[/]")
    rich_print(
        Panel.fit(
            Syntax(code=new_config_toml, lexer="toml", theme="harlequin"),
            border_style=YELLOW,
        )
    )

    all_good = questionary.confirm(
        "Save this profile?",
        style=HARLEQUIN_QUESTIONARY_STYLE,
    ).ask()

    if not all_good:
        raise KeyboardInterrupt()


def _validate_int(raw: str) -> bool:
    try:
        int(raw)
    except ValueError:
        return False
    else:
        return True


def _validate_dir_or_blank(raw: str) -> bool:
    if not raw:
        return True
    p = Path(raw)
    return p.exists() and p.is_dir()
