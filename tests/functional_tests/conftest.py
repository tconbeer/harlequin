from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def mock_user_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr("harlequin.cache.user_cache_dir", lambda **_: tmp_path)
    return tmp_path
