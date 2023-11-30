from __future__ import annotations

from pathlib import Path

import pytest
from harlequin.config import get_config_for_profile, load_config
from harlequin.exception import HarlequinConfigError


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
        isinstance(good_config["profiles"][name], dict) for name in expected_profiles  # type: ignore
    )
    assert good_config["profiles"]["my-duckdb-profile"]["limit"] == 200_000  # type: ignore


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_load_named_profile(data_dir: Path, filename: str) -> None:
    good_config_path = data_dir / "unit_tests" / "config" / filename
    config = get_config_for_profile(
        config_path=good_config_path, profile="local-postgres"
    )
    assert config["port"] == 5432
    assert config["theme"] == "fruity"


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_load_default_profile(data_dir: Path, filename: str) -> None:
    good_config_path = data_dir / "unit_tests" / "config" / filename
    config = get_config_for_profile(config_path=good_config_path, profile=None)
    assert config["adapter"] == "duckdb"
    assert config["theme"] == "monokai"


@pytest.mark.parametrize(
    "filename,key_words",
    [
        ("default_no_exist.toml", ["default_profile", "foo"]),
        ("extra_key.toml", ["unexpected key"]),
        ("none_profile.toml", ["None", "not allowed"]),
        ("not_toml.toml", ["TOML"]),
        ("profiles_not_table.toml", ["profiles", "key", "table"]),
        ("profile_not_table.toml", ["members", "profiles", "table"]),
        ("bad_option_name.toml", ["option", "invalid", "read-only", "read_only"]),
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
