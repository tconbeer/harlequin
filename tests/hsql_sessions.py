"""A `hsql --serve` process for tests to talk to, in a fresh interpreter.

Not in-process: a server swaps the process's streams, environment and working
directory for the length of every request, and pytest owns all three.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

from harlequin.hsql.session import socket_path

HsqlSubprocess = Callable[..., "subprocess.CompletedProcess[bytes]"]
"""One invocation of the console script, in a fresh interpreter, as bytes."""


class WarmSession:
    """One session, and the environment a client reaches it through."""

    def __init__(
        self,
        name: str,
        serve_argv: Sequence[str],
        *,
        runtime_dir: Path,
        home: Path,
    ) -> None:
        self.name = name
        self.runtime_dir = runtime_dir
        self.env = {"XDG_RUNTIME_DIR": str(runtime_dir)}
        self.stderr_path = runtime_dir / f"{name}.stderr"
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys\n"
                f"sys.argv = ['hsql', '--serve', {name!r}, *{list(serve_argv)!r}]\n"
                "from harlequin.hsql import main\n"
                "main()\n",
            ],
            stdout=subprocess.PIPE,
            stderr=self.stderr_path.open("wb"),
            cwd=home,
            env={
                **{
                    key: value
                    for key, value in os.environ.items()
                    if key not in ("HSQL_SESSION", "NO_COLOR")
                },
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home / "xdg"),
                **self.env,
            },
        )

    @property
    def socket_path(self) -> Path:
        return Path(socket_path(self.name, self.env))

    def wait_until_ready(self, timeout: float = 30.0) -> None:
        """Block until the server answers a connection, or it exited."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"the server exited {self.process.returncode}: {self.stderr()}"
                )
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(str(self.socket_path))
            except OSError:
                time.sleep(0.05)
                continue
            finally:
                probe.close()
            return
        raise RuntimeError(f"the server never listened: {self.stderr()}")

    def stderr(self) -> str:
        return self.stderr_path.read_text(encoding="utf-8", errors="replace")

    def stop(self, timeout: float = 30.0) -> int:
        """Stop it the way an operator does, and return its exit code."""
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        try:
            return self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait(5)
        finally:
            if self.process.stdout is not None:
                self.process.stdout.close()


ServeSession = Callable[..., WarmSession]
"""Start a session under a name, and stop it when the test is done."""
