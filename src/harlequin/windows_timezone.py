import tarfile
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

import platformdirs
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.lib as pl

from harlequin.exception import HarlequinTzDataError, pretty_print_warning

HARLEQUIN_TZ_DATA_PATH = platformdirs.user_data_path(appname="harlequin") / "tzdata"


def check_and_install_tzdata() -> None:
    """
    On Windows, Arrow expects to find a timezone database in the User's
    Downloads folder. We check to see if it can find one there,
    and if not, we override the tz db search path to a Harlequin-specific
    location. If it still can't find one, we download the DB to that
    harlequin-specific location.
    """
    try:
        pc.assume_timezone(datetime(2024, 1, 1), "America/New_York")
    except pl.ArrowInvalid:
        # no tz database in the default location; try the harlequin location
        try:
            pa.set_timezone_db_path(str(HARLEQUIN_TZ_DATA_PATH))
            pc.assume_timezone(datetime(2024, 1, 1), "America/New_York")
        except (OSError, pl.ArrowInvalid):
            message = (
                "Harlequin could not find a timezone database, which it needs "
                "to support Apache Arrow's timestamptz features. It is downloading one "
                "now (it will only do this once). For more info, see "
                "[link]https://harlequin.sh/docs/troubleshooting/timezone-windows[/]"
            )
            pretty_print_warning(title="Harlequin Timezone Support", message=message)
            try:
                # tz database is missing. We need to install it.
                response = urlopen(
                    "https://www.iana.org/time-zones/repository/tzdata-latest.tar.gz"
                )
                with TemporaryDirectory() as tmpdir:
                    tar_path = Path(tmpdir) / "tzdata.tar.gz"
                    with tar_path.open("wb") as f:
                        f.write(response.read())
                    tarfile.open(tar_path).extractall(HARLEQUIN_TZ_DATA_PATH)
                zone_response = urlopen(
                    "https://raw.githubusercontent.com/unicode-org/cldr/main/common/"
                    "supplemental/windowsZones.xml"
                )
                zone_target = HARLEQUIN_TZ_DATA_PATH / "windowsZones.xml"
                with zone_target.open("wb") as f:
                    f.write(zone_response.read())
                pa.set_timezone_db_path(str(HARLEQUIN_TZ_DATA_PATH))
            except Exception as e:
                err_msg = (
                    "Harlequin was not able to download a timezone database. Without "
                    "a timezone database, Harlequin may crash if you attempt to load "
                    "timestamptz values into the results viewer. To use Harlequin "
                    "anyway, set the --no-download-tzdata option.\n"
                    "For more info, see "
                    "[link]https://harlequin.sh/docs/troubleshooting/timezone-windows[/]"
                    f"\nDownload failed with the following exception:\n{e}"
                )
                raise HarlequinTzDataError(
                    msg=err_msg, title="Harlequin Timezone Error"
                ) from e
