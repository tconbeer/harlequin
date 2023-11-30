from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def mock_user_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr("harlequin.cache.user_cache_dir", lambda **_: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def mock_config_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("harlequin.cli.get_config_for_profile", lambda **_: dict())
