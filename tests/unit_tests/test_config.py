from __future__ import annotations

from pathlib import Path

import pytest

from harlequin.config import (
    _find_config_files,
    get_config_for_profile,
    get_highest_priority_existing_config_file,
    load_config,
)
from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyBinding, HarlequinKeyMap


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_load_config(data_dir: Path, filename: str) -> None:
    good_config_path = data_dir / "unit_tests" / "config" / filename
    good_config = load_config(config_path=good_config_path)
    assert isinstance(good_config, dict)
    assert "default_profile" in good_config
    assert good_config["default_profile"] == "my-duckdb-profile"
    assert "profiles" in good_config
    expected_profiles = ["my-duckdb-profile", "local-postgres"]
    assert all(name in good_config["profiles"] for name in expected_profiles)
    assert all(
        isinstance(good_config["profiles"][name], dict) for name in expected_profiles
    )
    assert good_config["profiles"]["my-duckdb-profile"]["limit"] == 200_000
    assert good_config["profiles"]["my-duckdb-profile"]["indent_width"] == 2


def test_load_keymap(data_dir: Path) -> None:
    good_config_path = data_dir / "unit_tests" / "config" / "keymaps.toml"
    keymap_name = "more_arrows"
    good_config = load_config(config_path=good_config_path)
    assert isinstance(good_config, dict)
    assert "keymaps" in good_config
    assert isinstance(good_config["keymaps"], dict)
    assert keymap_name in good_config["keymaps"]
    assert isinstance(good_config["keymaps"][keymap_name], list)
    assert isinstance(good_config["keymaps"][keymap_name][0], dict)
    assert all(
        [
            k in good_config["keymaps"][keymap_name][0]
            for k in ["keys", "action", "key_display"]
        ]
    )

    _, keymaps = get_config_for_profile(config_path=good_config_path, profile_name=None)
    assert len(keymaps) == 1
    assert isinstance(keymaps[0], HarlequinKeyMap)
    assert keymaps[0].name == keymap_name
    assert len(keymaps[0].bindings) == 4
    assert isinstance(keymaps[0].bindings[0], HarlequinKeyBinding)


@pytest.mark.parametrize(
    "filename,key_words",
    [
        ("keymaps_bad_binding.toml", ["Key bindings", "foo"]),
    ],
)
def test_load_bad_keymap_raises(
    data_dir: Path,
    filename: str,
    key_words: list[str],
) -> None:
    config_path = data_dir / "unit_tests" / "config" / filename
    with pytest.raises(HarlequinConfigError) as exc_info:
        _ = get_config_for_profile(config_path=config_path, profile_name=None)
    err = exc_info.value
    print(err)
    assert isinstance(err, HarlequinConfigError)
    assert "keymap" in err.title
    assert all([w in err.msg for w in key_words])


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_load_named_profile(data_dir: Path, filename: str) -> None:
    good_config_path = data_dir / "unit_tests" / "config" / filename
    profile, keymaps = get_config_for_profile(
        config_path=good_config_path, profile_name="local-postgres"
    )
    assert profile["port"] == 5432  # type: ignore[typeddict-item]
    assert profile["theme"] == "fruity"


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_load_default_profile(data_dir: Path, filename: str) -> None:
    good_config_path = data_dir / "unit_tests" / "config" / filename
    profile, keymaps = get_config_for_profile(
        config_path=good_config_path, profile_name=None
    )
    assert profile["adapter"] == "duckdb"
    assert profile["theme"] == "monokai"


@pytest.mark.parametrize(
    "filename,key_words",
    [
        ("default_no_exist.toml", ["default_profile", "foo"]),
        ("extra_key.toml", ["unexpected key"]),
        ("none_profile.toml", ["None", "not allowed"]),
        ("not_toml.toml", ["Attempted to load"]),
        ("profiles_not_table.toml", ["profiles", "key", "table"]),
        ("profile_not_table.toml", ["members", "profiles", "table"]),
        ("bad_option_name.toml", ["option", "invalid", "read-only", "read_only"]),
        ("keymaps_not_array.toml", ["keymaps", "array"]),
    ],
)
def test_bad_config_raises(
    data_dir: Path,
    filename: str,
    key_words: list[str],
) -> None:
    config_path = data_dir / "unit_tests" / "config" / filename
    with pytest.raises(HarlequinConfigError) as exc_info:
        _ = load_config(config_path=config_path)
    err = exc_info.value
    assert isinstance(err, HarlequinConfigError)
    assert "config" in err.title
    assert all([w in err.msg for w in key_words])


def test_config_file_discovery(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # first, patch the real search paths with tmps
    mock_home = tmp_path_factory.mktemp("home")
    mock_config = tmp_path_factory.mktemp("config")
    mock_cwd = tmp_path_factory.mktemp("cwd")
    custom = tmp_path_factory.mktemp("custom") / "foo.toml"

    # create empty config files in our mock dirs
    expected_paths = [
        mock_home / "pyproject.toml",
        mock_home / ".harlequin.toml",
        mock_config / "config.toml",
        mock_config / "harlequin.toml",
        mock_cwd / "pyproject.toml",
        mock_cwd / ".harlequin.toml",
        mock_cwd / "harlequin.toml",
        custom,
    ]
    for p in expected_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.open("w").close()

    monkeypatch.setattr(Path, "cwd", lambda: mock_cwd)
    monkeypatch.setattr(Path, "home", lambda: mock_home)
    monkeypatch.setattr("harlequin.config.user_config_path", lambda **_: mock_config)

    assert _find_config_files(config_path=custom) == expected_paths

    expected_paths.pop()
    assert _find_config_files(config_path=None) == expected_paths
    assert get_highest_priority_existing_config_file() == expected_paths[-1]
