from pathlib import Path
from typing import Tuple


def get_init_script(init_path: Path, no_init: bool) -> Tuple[Path, str]:
    if no_init:
        init_script = ""
    else:
        try:
            with open(init_path.expanduser(), "r") as f:
                init_script = f.read()
        except OSError:
            init_script = ""
    return init_path, init_script
