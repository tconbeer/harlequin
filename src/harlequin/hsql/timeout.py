"""A wall clock over a run, and how it exits.

A deadline is a promise about elapsed time, and the only thing that can keep it
is the adapter's own `cancel()` -- so `--timeout` is refused where
`IMPLEMENTS_CANCEL` is False rather than counting down over work nothing can
stop. The work runs on a worker thread and the clock on the main one, because
that is the only arrangement where something is still awake to notice the
clock ran out.

Two things about an expiry are not obvious, and both were measured (§1.9 of the
M2 plan):

- **A cancelled query comes back empty and error-free.** DuckDB's cursor
  catches the interrupt and returns no rows, which is also what a query that
  matched nothing looks like, so nothing downstream can tell the two apart. The
  run reads `expired` and stops rather than printing the empty result the
  cancel produced, and hsql attributes the timeout itself.
- **Exiting while a worker is still inside the driver aborts the process.**
  `sys.exit()` with a live thread in duckdb's `to_arrow_table()` exits 134,
  which is not a code hsql documents. So a grace period that runs out ends in
  `os._exit()`, with both streams flushed by hand -- the one place in the
  command where the interpreter is not given the chance to unwind.

`SIGINT` lands on the main thread, which is here, so it takes the same path and
exits 130.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from typing import TYPE_CHECKING, Callable, NoReturn, TypeVar

from harlequin.hsql import diagnostics
from harlequin.hsql.diagnostics import ExitCode

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinConnection

T = TypeVar("T")

GRACE_SECONDS = 5.0
"""How long a cancelled run has to unwind before the process stops waiting."""


class TimedOut(Exception):
    """The clock ran out, and the work stopped when it was asked to."""


class Deadline:
    """How long a run may take, and whether its time is up."""

    def __init__(self, seconds: float, *, grace: float | None = None) -> None:
        self.seconds = seconds
        self.grace = GRACE_SECONDS if grace is None else grace
        self._expired = threading.Event()

    @property
    def expired(self) -> bool:
        """Whether the clock ran out. Read by the run, between results."""
        return self._expired.is_set()

    def run(self, work: Callable[[], T], *, connection: HarlequinConnection) -> T:
        """Run `work` under the clock, and return what it returned.

        Raises: TimedOut if the clock ran out, and whatever `work` raised
        otherwise. Does not return at all if cancelled work outlasts the grace
        period.
        """
        done: list[T] = []
        failure: list[BaseException] = []
        finished = threading.Event()

        def target() -> None:
            try:
                done.append(work())
            except BaseException as e:  # noqa: BLE001 -- raised on the main thread
                failure.append(e)
            finally:
                finished.set()

        worker = threading.Thread(target=target, name="hsql-run", daemon=True)
        worker.start()
        try:
            in_time = finished.wait(self.seconds)
        except KeyboardInterrupt:
            # the same cancel, and 130 rather than 4: an interrupted run is one
            # the caller stopped, not one that ran too long
            self._cancel(connection)
            if not finished.wait(self.grace):
                _halt(ExitCode.INTERRUPT)
            raise
        if not in_time:
            self._cancel(connection)
            if not finished.wait(self.grace):
                diagnostics.report_timeout(self.seconds)
                _halt(ExitCode.TIMEOUT)
            raise TimedOut()
        if failure:
            raise failure[0]
        return done[0]

    def _cancel(self, connection: HarlequinConnection) -> None:
        """Ask the database to stop, having marked the run over.

        In that order: the flag is what the run reads to tell the empty result
        set a cancel produces from one a query really returned, and it has to
        be set before anything can observe the cancel.
        """
        self._expired.set()
        with contextlib.suppress(Exception):  # adapters are third-party code
            # whatever it raised, the clock has run out either way, and the
            # grace period is what decides how this ends
            connection.cancel()


def _halt(code: ExitCode) -> NoReturn:
    """End the process now, around a worker thread that would abort it."""
    with contextlib.suppress(Exception):
        # `os._exit()` runs no atexit handler, and an ssh child that outlives
        # the run is the thing the tunnel feature exists to remove
        from harlequin.ssh import stop_all

        stop_all()
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    os._exit(code)
