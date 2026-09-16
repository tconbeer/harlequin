from __future__ import annotations

import io
import tarfile
import threading
import time
from typing import Any
from urllib.error import URLError

import pytest

from harlequin.exception import HarlequinTzDataError
from harlequin.windows_timezone import (
    DOWNLOAD_DEADLINE_SECONDS,
    MAX_DOWNLOAD_BYTES,
    SOCKET_TIMEOUT_SECONDS,
    download_tzdata,
)


def _empty_tar_gz() -> bytes:
    """A real, empty .tar.gz, so a fake first response gets past extraction."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz"):
        pass
    return buffer.getvalue()


class _Trickle(io.RawIOBase):
    """A peer that keeps the socket busy without ever finishing, which is what
    a per-operation timeout cannot catch."""

    def read(self, size: int = -1) -> bytes:
        time.sleep(0.005)
        return b"x"


def test_every_request_has_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both requests, not just the first: the one after a successful tarball is
    where a user is most likely to be sitting when the network goes bad."""
    timeouts: list[Any] = []

    def fake_urlopen(url: str, *args: Any, **kwargs: Any) -> Any:
        timeouts.append(kwargs.get("timeout"))
        if len(timeouts) == 1:
            # a real, empty gzip member, so the tarball step gets past extraction
            return io.BytesIO(_empty_tar_gz())
        raise URLError("no network")

    monkeypatch.setattr("harlequin.windows_timezone.urlopen", fake_urlopen)
    with pytest.raises(HarlequinTzDataError):
        download_tzdata()
    assert timeouts == [SOCKET_TIMEOUT_SECONDS, SOCKET_TIMEOUT_SECONDS]


def test_a_trickling_peer_hits_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The socket timeout is per-operation, so only the overall deadline ends
    a transfer that never stalls but never finishes either."""
    monkeypatch.setattr(
        "harlequin.windows_timezone.DOWNLOAD_DEADLINE_SECONDS", 0.25, raising=True
    )
    monkeypatch.setattr(
        "harlequin.windows_timezone.urlopen",
        lambda *args, **kwargs: _Trickle(),
    )
    started = time.monotonic()
    with pytest.raises(HarlequinTzDataError) as excinfo:
        download_tzdata()
    assert time.monotonic() - started < DOWNLOAD_DEADLINE_SECONDS
    assert "did not finish" in str(excinfo.value)


def test_an_oversized_response_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Firehose(io.RawIOBase):
        def read(self, size: int = -1) -> bytes:
            return b"x" * (1024 * 1024)

    monkeypatch.setattr(
        "harlequin.windows_timezone.urlopen", lambda *args, **kwargs: _Firehose()
    )
    with pytest.raises(HarlequinTzDataError) as excinfo:
        download_tzdata()
    assert str(MAX_DOWNLOAD_BYTES) in str(excinfo.value)


def test_stop_abandons_the_download_without_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quitting is not a failure to report, and it must not wait for the peer."""
    monkeypatch.setattr(
        "harlequin.windows_timezone.urlopen", lambda *args, **kwargs: _Trickle()
    )
    stop = threading.Event()
    stop.set()
    download_tzdata(stop=stop)


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
