"""Every wait the tests do, bounded and in one place.

A wait on a duration asserts the machine is fast enough. These poll a
condition instead, so a loaded runner makes a test slower rather than red.

`wait_until` polls state another thread or process sets; the `wait_for_*`
coroutines poll state the app sets, pumping its messages as they go. `settle`
is the only duration: establishing that nothing *else* happens takes one.
"""

from __future__ import annotations

import os
import random
import socket
import time
from typing import TYPE_CHECKING, Callable, Sequence, TypeVar

if TYPE_CHECKING:
    from textual.message import Message
    from textual.pilot import Pilot

T = TypeVar("T")
MessageT = TypeVar("MessageT", bound="Message")

TIMEOUT = 10.0
"""Seconds any one wait may take before the test has failed."""

POLL_INTERVAL = 0.02
"""Seconds between polls of something this thread cannot await."""

SETTLE_SECONDS = 0.3
"""How long "and then nothing else happened" takes to establish."""


def wait_until(predicate: Callable[[], bool], *, seconds: float = TIMEOUT) -> bool:
    """Poll until something another thread or process does becomes true."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL_INTERVAL)
    return predicate()


def settle(seconds: float = SETTLE_SECONDS) -> None:
    """Give whatever must not happen time to happen, before asserting it did not."""
    time.sleep(seconds)


async def wait_for(
    pilot: Pilot,
    predicate: Callable[[], bool],
    *,
    description: str,
    seconds: float = TIMEOUT,
    interval: float | None = None,
) -> None:
    """Pump the app until `predicate` holds, or fail saying what never happened.

    `interval` is for a condition a thread sets, which no amount of pumping
    brings closer; the default hands the loop back until the app is idle.
    """
    deadline = time.monotonic() + seconds
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"timed out after {seconds}s waiting for {description}"
            )
        await pilot.pause(interval)


async def wait_for_value(
    pilot: Pilot,
    get: Callable[[], T | None],
    *,
    description: str,
    seconds: float = TIMEOUT,
    interval: float | None = None,
) -> T:
    """The value `get` returns once it is not None, pumping the app until it is."""
    value: T | None = None

    def arrived() -> bool:
        nonlocal value
        value = get()
        return value is not None

    await wait_for(
        pilot, arrived, description=description, seconds=seconds, interval=interval
    )
    assert value is not None
    return value


async def wait_for_messages(
    pilot: Pilot,
    messages: Sequence[Message],
    message_type: type[MessageT],
    *,
    count: int = 1,
    seconds: float = TIMEOUT,
) -> list[MessageT]:
    """The first `count` messages of a type a `message_hook` has collected.

    A message is posted a turn or more before the app handles it, so the list a
    hook fills is state to wait on like any other.
    """

    def collected() -> list[MessageT]:
        return [m for m in messages if isinstance(m, message_type)]

    await wait_for(
        pilot,
        lambda: len(collected()) >= count,
        description=(
            f"{count} {message_type.__name__} message"
            f"{'' if count == 1 else 's'}, saw {len(collected())}"
        ),
        seconds=seconds,
    )
    return collected()[:count]


_PORT_RANGE = (20000, 30000)
"""Where a test's ports come from: below the ephemeral range every supported
platform hands out on its own (32768 on Linux, 49152 on macOS and Windows), so
only something that asked for a port by number competes for one."""

_handed_out: set[int] = set()
"""Ports this process has already named, which it must not name twice."""

_draw = random.Random(os.getpid())
"""Seeded per process, so two xdist workers walk different candidates."""


def free_port() -> int:
    """A loopback port nothing is listening on, that nothing is about to take.

    Binding port 0 and closing it hands the port back to the pool the OS draws
    from for every outbound connection, so the next one the machine makes can
    take it before the child that was given it binds it -- which on a loaded
    runner is a test that fails for a reason nothing in it explains. Drawing
    from outside that pool leaves only another test as a competitor, and a
    caller that hands the port to a child still has `on_a_free_port`.
    """
    low, high = _PORT_RANGE
    for _ in range(100):
        port = _draw.randrange(low, high)
        if port in _handed_out:
            continue
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        _handed_out.add(port)
        return port
    raise AssertionError(f"no free port between {low} and {high}")


def accepts(port: int, *, timeout: float = 1.0) -> bool:
    """Whether a loopback port accepts a connection right now."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def on_a_free_port(
    start: Callable[[int], T],
    *,
    retry_on: type[BaseException] | tuple[type[BaseException], ...],
    attempts: int = 5,
) -> T:
    """Call `start` with a free port, retrying when something else took it.

    `free_port` can only report a port that was free a moment ago, so a child
    that binds it races every other process on the machine for it. Losing that
    race raises `retry_on`, and the answer to it is another port.
    """
    for attempt in range(attempts):
        try:
            return start(free_port())
        except retry_on:
            if attempt == attempts - 1:
                raise
    raise AssertionError("unreachable")
