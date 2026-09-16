"""Every wait the tests do, bounded and in one place.

`wait_until` polls state another thread or process sets; the `wait_for_*`
coroutines poll state the app sets, pumping its messages as they go. `settle`
is the only duration: establishing that nothing *else* happens takes one.
"""

from __future__ import annotations

import random
import socket
import time
from typing import TYPE_CHECKING, Callable, Sequence, TypeVar, Union

if TYPE_CHECKING:
    from textual.message import Message
    from textual.pilot import Pilot

T = TypeVar("T")
MessageT = TypeVar("MessageT", bound="Message")

Description = Union[str, Callable[[], str]]
"""What a wait was for. A callable is resolved at the raise, so it can report
the state the wait gave up on rather than the state it started from."""

TIMEOUT = 10.0
"""Seconds any one wait may take before the test has failed."""

POLL_INTERVAL = 0.02
"""Seconds between polls of something this thread cannot await."""

SETTLE_SECONDS = 0.3
"""How long "and then nothing else happened" takes to establish."""


def _timed_out(seconds: float, description: Description) -> AssertionError:
    resolved = description() if callable(description) else description
    return AssertionError(f"timed out after {seconds}s waiting for {resolved}")


def wait_until(
    predicate: Callable[[], bool], *, description: Description, seconds: float = TIMEOUT
) -> None:
    """Poll until something another thread or process does becomes true."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(POLL_INTERVAL)
    if not predicate():
        raise _timed_out(seconds, description)


def settle(seconds: float = SETTLE_SECONDS) -> None:
    """Give whatever must not happen time to happen, before asserting it did not."""
    time.sleep(seconds)


async def settle_app(pilot: Pilot, seconds: float = SETTLE_SECONDS) -> None:
    """`settle`, for an app that is running: a blocking sleep would stop the
    loop the thing that must not happen would have to happen on."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await pilot.pause(POLL_INTERVAL)


async def wait_for(
    pilot: Pilot,
    predicate: Callable[[], bool],
    *,
    description: Description,
    seconds: float = TIMEOUT,
    interval: float | None = None,
) -> None:
    """Pump the app until `predicate` holds, or fail saying what never happened.

    `interval` is for a condition a thread sets, where a blocking sleep would
    stop the app's own loop; the default hands the loop back until it is idle.
    """
    deadline = time.monotonic() + seconds
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise _timed_out(seconds, description)
        await pilot.pause(interval)


async def wait_for_value(
    pilot: Pilot,
    get: Callable[[], T | None],
    *,
    description: Description,
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
    exactly: bool = False,
    seconds: float = TIMEOUT,
) -> list[MessageT]:
    """The first `count` messages of a type a `message_hook` has collected.

    `exactly` also asserts there is no `count + 1`th, which is what a test
    proving one keypress ran one query and not two is asserting.
    """

    def collected() -> list[MessageT]:
        return [m for m in messages if isinstance(m, message_type)]

    await wait_for(
        pilot,
        lambda: len(collected()) >= count,
        description=lambda: (
            f"{count} {message_type.__name__} message"
            f"{'' if count == 1 else 's'}, saw {len(collected())}"
        ),
        seconds=seconds,
    )
    if exactly:
        assert len(collected()) == count, (
            f"expected {count} {message_type.__name__} message"
            f"{'' if count == 1 else 's'}, got {len(collected())}"
        )
    return collected()[:count]


_PORT_RANGE = (20000, 30000)
"""Where a test's ports come from: below the ephemeral range every supported
platform hands out on its own (32768 on Linux, 49152 on macOS and Windows), so
only something that asked for a port by number competes for one."""

_handed_out: set[int] = set()
"""Ports this process has already named, which it must not name twice."""

_draw = random.Random()
"""Seeded from the OS, so two xdist workers walk different candidates."""


def free_port() -> int:
    """A loopback port nothing is listening on, drawn from below the ephemeral range.

    It can only report a port that was free a moment ago; a caller that hands
    one to a child has `on_a_free_port`.
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
    retry_on: Callable[[BaseException], bool],
    attempts: int = 5,
) -> T:
    """Call `start` with a free port, retrying while `retry_on` says it lost it.

    `retry_on` reads the exception rather than naming its type, because the one
    a driver raises for a port it could not take is the one it raises for
    everything else, and retrying a real failure five times is five timeouts.
    """
    for attempt in range(attempts):
        try:
            return start(free_port())
        except BaseException as e:
            if attempt == attempts - 1 or not retry_on(e):
                raise
    raise AssertionError("unreachable")
