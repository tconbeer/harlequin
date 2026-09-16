from __future__ import annotations

from typing import Any
from urllib.error import URLError

import pytest

from harlequin.exception import HarlequinTzDataError
from harlequin.windows_timezone import DOWNLOAD_TIMEOUT_SECONDS, download_tzdata


def test_every_request_has_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network that hangs rather than fails would otherwise never return."""
    timeouts: list[Any] = []

    def fake_urlopen(url: str, *args: Any, **kwargs: Any) -> Any:
        timeouts.append(kwargs.get("timeout"))
        raise URLError("no network")

    monkeypatch.setattr("harlequin.windows_timezone.urlopen", fake_urlopen)
    with pytest.raises(HarlequinTzDataError):
        download_tzdata()
    assert timeouts == [DOWNLOAD_TIMEOUT_SECONDS]


def test_a_failed_download_names_the_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(url: str, *args: Any, **kwargs: Any) -> Any:
        raise URLError("no network")

    monkeypatch.setattr("harlequin.windows_timezone.urlopen", fake_urlopen)
    with pytest.raises(HarlequinTzDataError) as excinfo:
        download_tzdata()
    assert "no network" in str(excinfo.value)
    assert "--no-download-tzdata" in str(excinfo.value)
