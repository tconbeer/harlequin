"""Run Harlequin against a real database and report event-loop lag on exit.

Event-loop lag is what "the catalog is hanging" actually means: every millisecond
the loop is blocked is a millisecond of unrendered keystrokes and scrolling.

Usage:
    uv run python scripts/catalog_lag.py --adapter postgres "postgresql://..."
    uv run python scripts/catalog_lag.py --profile my-big-mysql

Browse the catalog as you normally would -- expand big schemas, scroll fast --
then quit with ctrl+q. Anything over ~100ms at p95 is visible jank.
"""

from __future__ import annotations

import asyncio
import sys
import time

from harlequin.app import Harlequin

SAMPLE_INTERVAL = 0.05


class Heartbeat:
    """Sample how late a fixed-interval timer actually fires."""

    def __init__(self) -> None:
        self.lags: list[float] = []

    async def run(self) -> None:
        while True:
            start = time.monotonic()
            await asyncio.sleep(SAMPLE_INTERVAL)
            self.lags.append(time.monotonic() - start - SAMPLE_INTERVAL)

    def report(self) -> str:
        if not self.lags:
            return "no samples collected"
        lags = sorted(self.lags)

        def pct(p: float) -> float:
            return lags[min(int(len(lags) * p), len(lags) - 1)] * 1000

        over_100ms = sum(1 for lag in lags if lag > 0.1)
        return (
            f"\nevent-loop lag over {len(lags)} samples "
            f"({len(lags) * SAMPLE_INTERVAL:.0f}s of use):\n"
            f"  p50 {pct(0.50):6.1f} ms\n"
            f"  p95 {pct(0.95):6.1f} ms\n"
            f"  p99 {pct(0.99):6.1f} ms\n"
            f"  max {lags[-1] * 1000:6.1f} ms\n"
            f"  {over_100ms} samples ({over_100ms / len(lags):.1%}) over 100ms\n"
        )


def main() -> None:
    heartbeat = Heartbeat()
    original_on_mount = Harlequin.on_mount

    async def on_mount(self: Harlequin) -> None:
        asyncio.create_task(heartbeat.run())
        await original_on_mount(self)

    Harlequin.on_mount = on_mount  # type: ignore[method-assign]

    from harlequin.cli import harlequin

    try:
        # `harlequin` is a click command; the decorator hides `.main` from mypy
        harlequin.main(sys.argv[1:], standalone_mode=False)  # type: ignore[attr-defined]
    finally:
        print(heartbeat.report(), file=sys.stderr)


if __name__ == "__main__":
    main()
