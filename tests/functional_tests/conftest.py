from pathlib import Path

import pytest


@pytest.fixture(autouse=False)
def mock_user_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr("harlequin.cache.user_cache_dir", lambda **_: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def no_use_buffer_cache(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if "use_cache" in request.keywords:
        return
    monkeypatch.setattr("harlequin.components.code_editor.load_cache", lambda: None)
    monkeypatch.setattr("harlequin.app.write_cache", lambda *_: None)


@pytest.fixture(autouse=True)
def mock_config_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("harlequin.cli.get_config_for_profile", lambda **_: dict())
