from __future__ import annotations

import subprocess
import sys
from typing import Callable

import pytest


@pytest.fixture
def run_python() -> Callable[[str], subprocess.CompletedProcess[str]]:
    """Run a snippet in a fresh interpreter and capture its streams.

    In-process assertions can't see the state these tests care about: which
    modules an import pulled in, and which stream something was written to.
    Both are properties of a clean interpreter, and pytest has already imported
    half the world by the time a test runs.
    """

    def _run(code: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
        )

    return _run
