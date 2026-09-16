from __future__ import annotations

import tarfile
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

import platformdirs
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.lib as pl

from harlequin.exception import HarlequinTzDataError

HARLEQUIN_TZ_DATA_PATH = platformdirs.user_data_path(appname="harlequin") / "tzdata"

DOWNLOAD_TIMEOUT_SECONDS = 30.0
"""Given to every request: urlopen has no timeout of its own, so a network that
hangs instead of failing would never hand the thread back."""


def find_tzdata() -> bool:
    """
    On Windows, Arrow expects to find a timezone database in the User's
    Downloads folder. We check to see if it can find one there, and if not, we
    override the tz db search path to a Harlequin-specific location. Returns
    whether Arrow found a database in either place.
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


def download_tzdata() -> None:
    """
    Download the IANA timezone database into the Harlequin-specific location
    and point Arrow at it. Raises HarlequinTzDataError if it cannot.
    """
    try:
        response = urlopen(
            "https://www.iana.org/time-zones/repository/tzdata-latest.tar.gz",
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
        with TemporaryDirectory() as tmpdir:
            tar_path = Path(tmpdir) / "tzdata.tar.gz"
            with tar_path.open("wb") as f:
                f.write(response.read())
            tarfile.open(tar_path).extractall(HARLEQUIN_TZ_DATA_PATH)
        zone_response = urlopen(
            "https://raw.githubusercontent.com/unicode-org/cldr/main/common/"
            "supplemental/windowsZones.xml",
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
        zone_target = HARLEQUIN_TZ_DATA_PATH / "windowsZones.xml"
        with zone_target.open("wb") as f:
            f.write(zone_response.read())
        pa.set_timezone_db_path(str(HARLEQUIN_TZ_DATA_PATH))
    except Exception as e:
        err_msg = (
            "Harlequin was not able to download a timezone database. Without "
            "a timezone database, Harlequin may crash if you attempt to load "
            "timestamptz values into the results viewer. To start Harlequin "
            "without checking for one, set the --no-download-tzdata option.\n"
            "For more info, see "
            "https://harlequin.sh/docs/troubleshooting/timezone-windows\n"
            f"Download failed with the following exception:\n{e}"
        )
        raise HarlequinTzDataError(msg=err_msg, title="Harlequin Timezone Error") from e
