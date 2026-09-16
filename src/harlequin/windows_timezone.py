from __future__ import annotations

import tarfile
import threading
import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import IO
from urllib.request import urlopen

import platformdirs
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.lib as pl

from harlequin.exception import HarlequinTzDataError

HARLEQUIN_TZ_DATA_PATH = platformdirs.user_data_path(appname="harlequin") / "tzdata"

SOCKET_TIMEOUT_SECONDS = 10.0
"""Given to every request. urlopen applies it per socket operation, so it ends
a stalled connect or a stalled packet, but never a whole transfer."""

DOWNLOAD_DEADLINE_SECONDS = 30.0
"""The budget for the whole download, checked between chunks. A peer that
trickles one byte at a time satisfies the socket timeout forever, so this is
what actually ends such a transfer."""

MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
"""Refuses a response far larger than the ~0.5MB these files are, so a hostile
host cannot fill the disk."""

_CHUNK_BYTES = 64 * 1024

TZ_DATA_DOCS_URL = "https://harlequin.sh/docs/troubleshooting/timezone-windows"


class _Abandoned(Exception):
    """The caller asked for the download to stop; nothing to report."""


def locate_tzdata() -> bool:
    """Whether Arrow can read a timezone database, from the user's Downloads
    folder or from Harlequin's own location.

    Leaves Arrow's search path pointed at Harlequin's location when Downloads
    has none, which is where a download would put one.
    """
    try:
        pc.assume_timezone(datetime(2024, 1, 1), "America/New_York")
    except pl.ArrowInvalid:
        # no tz database in the default location; try the harlequin location
        try:
            pa.set_timezone_db_path(str(HARLEQUIN_TZ_DATA_PATH))
            pc.assume_timezone(datetime(2024, 1, 1), "America/New_York")
        except (OSError, pl.ArrowInvalid):
            return False
    return True


def _read_bounded(
    response: IO[bytes], deadline: float, stop: threading.Event | None
) -> bytes:
    """Read a response in chunks, giving up on the deadline, the size cap or
    `stop`, none of which a socket timeout can express."""
    chunks: list[bytes] = []
    total = 0
    while True:
        if stop is not None and stop.is_set():
            raise _Abandoned()
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"the download did not finish within {DOWNLOAD_DEADLINE_SECONDS:.0f}s"
            )
        chunk = response.read(_CHUNK_BYTES)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"the download exceeded {MAX_DOWNLOAD_BYTES} bytes, "
                "which these files never are"
            )
        chunks.append(chunk)


def _extract(tar_path: Path) -> None:
    """Unpack the tarball, refusing a member that would land outside the
    destination or that is far larger than this archive really is."""
    with tarfile.open(tar_path) as tar:
        if sum(max(member.size, 0) for member in tar.getmembers()) > MAX_DOWNLOAD_BYTES:
            raise ValueError("the timezone archive unpacks to more than it should")
        if hasattr(tarfile, "data_filter"):
            # refuses absolute paths and `..`, so a hostile archive cannot
            # write outside HARLEQUIN_TZ_DATA_PATH. Added in 3.10.12.
            tar.extractall(HARLEQUIN_TZ_DATA_PATH, filter="data")
        else:
            tar.extractall(HARLEQUIN_TZ_DATA_PATH)  # noqa: S202


def download_tzdata(stop: threading.Event | None = None) -> None:
    """
    Download the IANA timezone database into the Harlequin-specific location
    and point Arrow at it. Raises HarlequinTzDataError if it cannot.

    `stop` abandons the download between chunks and returns, so a caller that
    is shutting down does not wait for the transfer to finish.
    """
    deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
    try:
        response = urlopen(
            "https://www.iana.org/time-zones/repository/tzdata-latest.tar.gz",
            timeout=SOCKET_TIMEOUT_SECONDS,
        )
        tarball = _read_bounded(response, deadline, stop)
        with TemporaryDirectory() as tmpdir:
            tar_path = Path(tmpdir) / "tzdata.tar.gz"
            with tar_path.open("wb") as f:
                f.write(tarball)
            _extract(tar_path)
        zone_response = urlopen(
            "https://raw.githubusercontent.com/unicode-org/cldr/main/common/"
            "supplemental/windowsZones.xml",
            timeout=SOCKET_TIMEOUT_SECONDS,
        )
        zone_target = HARLEQUIN_TZ_DATA_PATH / "windowsZones.xml"
        with zone_target.open("wb") as f:
            f.write(_read_bounded(zone_response, deadline, stop))
        pa.set_timezone_db_path(str(HARLEQUIN_TZ_DATA_PATH))
    except _Abandoned:
        return
    except Exception as e:
        err_msg = (
            "Harlequin was not able to download a timezone database. Without "
            "a timezone database, Harlequin may crash if you attempt to load "
            "timestamptz values into the results viewer. To start Harlequin "
            "without checking for one, set the --no-download-tzdata option.\n"
            f"For more info, see {TZ_DATA_DOCS_URL}\n"
            f"Download failed with the following exception:\n{e}"
        )
        raise HarlequinTzDataError(msg=err_msg, title="Harlequin Timezone Error") from e
