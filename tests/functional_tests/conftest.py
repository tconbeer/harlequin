from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def no_use_buffer_cache(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if "use_cache" in request.keywords:
        return
    monkeypatch.setattr("harlequin.components.code_editor.load_cache", lambda: None)
    monkeypatch.setattr("harlequin.app.write_editor_cache", lambda *_: None)


@pytest.fixture(autouse=True)
def no_use_catalog_cache(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if "use_cache" in request.keywords:
        return
    monkeypatch.setattr("harlequin.app.get_catalog_cache", lambda *_: None)
    monkeypatch.setattr("harlequin.app.update_catalog_cache", lambda *_: None)


@pytest.fixture(autouse=True)
def mock_config_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("harlequin.cli.get_config_for_profile", lambda **_: dict())


@pytest.fixture
def mock_pyperclip(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    mock.determine_clipboard.return_value = mock.copy, mock.paste

    def set_paste(x: str) -> None:
        mock.paste.return_value = x

    mock.copy.side_effect = set_paste
    monkeypatch.setattr("textual_textarea.text_editor.pyperclip", mock)

    return mock
