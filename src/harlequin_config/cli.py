from __future__ import annotations

from pathlib import Path

import questionary
from harlequin.options import HARLEQUIN_STYLE, ListOption
from harlequin.plugins import load_plugins
from pygments.styles import get_all_styles
from tomlkit.exceptions import TOMLKitError
from tomlkit.toml_document import TOMLDocument
from tomlkit.toml_file import TOMLFile


def wizard() -> None:
    try:
        _wizard()
    except KeyboardInterrupt:
        print("Cancelled config updates. No changes were made to any files.")
        return


def _wizard() -> None:
    raw_path: str = questionary.path(
        "What config file do you want to create or update?",
        default=".harlequin.toml",
        style=HARLEQUIN_STYLE,
    ).unsafe_ask()
    path = Path(raw_path)
    is_pyproject = path.stem == "pyproject"
    if path.suffix != ".toml":
        _print_error("Must create a .toml file.")
        return
    file = TOMLFile(path)
    try:
        config = file.read()
    except OSError:
        config = TOMLDocument()
    except TOMLKitError as e:
        _print_error(
            f"Attempted to load the config file at {path}, but encountered an "
            f"error:\n\n{e}"
        )
        return
    else:
        if is_pyproject:
            full_config = config
            config = config.get("tool", {}).get("harlequin", {})

    if "profiles" not in config:
        config["profiles"] = {}
    profiles = config.get("profiles", {})

    NEW_PROFILE_SENTINEL = "[Create a New Profile]"
    profile_name = NEW_PROFILE_SENTINEL
    if profiles:
        profile_name = questionary.select(
            message="Which profile would you like to update?",
            choices=[NEW_PROFILE_SENTINEL, *profiles.keys()],
            style=HARLEQUIN_STYLE,
        ).unsafe_ask()
    if profile_name == NEW_PROFILE_SENTINEL:
        profile_name = questionary.text(
            message="What would you like to name your profile?",
            style=HARLEQUIN_STYLE,
        ).unsafe_ask()

    selected_profile = profiles.get(profile_name, {})

    adapters = load_plugins()
    adapter = questionary.select(
        message="Which adapter should this profile use?",
        choices=sorted(adapters.keys()),
        default=selected_profile.get("adapter", "duckdb"),
        style=HARLEQUIN_STYLE,
    ).unsafe_ask()

    conn_str = questionary.text(
        message="What connection string(s) should this profile use?",
        instruction="Separate items by a space.",
        default=" ".join(selected_profile.get("conn_str", [])),
        style=HARLEQUIN_STYLE,
    ).unsafe_ask()

    theme = questionary.select(
        message="What theme should this profile use?",
        choices=list(get_all_styles()),
        default=selected_profile.get("theme", "monokai"),
        style=HARLEQUIN_STYLE,
    ).unsafe_ask()

    limit = int(
        questionary.text(
            message="How many rows should the data table show?",
            validate=_validate_int,
            default=str(selected_profile.get("limit", 100000)),
            style=HARLEQUIN_STYLE,
        ).unsafe_ask()
    )

    adapter_cls = adapters[adapter]
    adapter_option_choices = (
        [
            questionary.Choice(title=opt.name, checked=opt.name in selected_profile)
            for opt in adapter_cls.ADAPTER_OPTIONS
        ]
        if adapter_cls.ADAPTER_OPTIONS is not None
        else []
    )
    which = questionary.checkbox(
        message="Which of the following adapter options would you like to set?",
        choices=adapter_option_choices,
        style=HARLEQUIN_STYLE,
    ).unsafe_ask()

    adapter_options = {}
    if conn_str:
        adapter_options["conn_str"] = conn_str.split(" ")
    if which and adapter_cls.ADAPTER_OPTIONS is not None:
        for option in adapter_cls.ADAPTER_OPTIONS:
            if option.name not in which:
                continue
            value = option.to_questionary(
                selected_profile.get(option.name, None)
            ).unsafe_ask()
            if isinstance(option, ListOption):
                value = value.split(" ")
            adapter_options.update({option.name.strip("-").replace("-", "_"): value})

    possible_names = set([profile_name, *profiles.keys()])
    NO_DEFAULT_SENTINEL = "[No default]"
    default_profile = questionary.select(
        message="Would you like to set a default profile?",
        choices=[
            NO_DEFAULT_SENTINEL,
            *possible_names,
        ],
        default=config.get("default_profile", None),
        style=HARLEQUIN_STYLE,
    ).unsafe_ask()

    if default_profile == NO_DEFAULT_SENTINEL:
        _ = config.pop("default_profile", None)
    else:
        config["default_profile"] = default_profile

    config["profiles"][profile_name] = {  # type: ignore
        "adapter": adapter,
        "theme": theme,
        "limit": limit,
        **adapter_options,
    }

    if is_pyproject:
        if "tool" not in full_config:
            full_config["tool"] = {}
        full_config["tool"]["harlequin"] = config  # type: ignore
        config = full_config

    file.write(config)


def _print_error(message: str) -> None:
    from rich import print as rich_print

    rich_print(f"[red bold]Error![/] {message}")


def _validate_int(raw: str) -> bool:
    try:
        int(raw)
    except ValueError:
        return False
    else:
        return True
