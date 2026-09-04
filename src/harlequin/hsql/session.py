"""Which session an invocation belongs to, and where that session's socket is.

The first thing `main()` reads, on an invocation that may turn out to be served
by a warm session rather than by a fresh process -- so it imports nothing. A
whole warm round trip is about a millisecond and the client's cost is almost
entirely the interpreter starting, which makes every import on this path a
larger number than the feature itself.

POSIX only: a session is reached over an `AF_UNIX` socket, which CPython does
not have on native Windows. The client checks that before it asks for a path.
"""

from __future__ import annotations

import os
import stat

TYPE_CHECKING = False
"""Every annotation here is a string (PEP 563), so `typing` stays off this path."""

if TYPE_CHECKING:
    from typing import Mapping, Sequence

SESSION_ENV_VAR = "HSQL_SESSION"
"""The ambient spelling: a preference, so an invocation still runs without one."""

SESSION_OPTION = "--session"
"""The typed spelling: an assertion, so an invocation fails without one."""

MAX_NAME_LENGTH = 64

MAX_SOCKET_PATH = 104
"""The bytes a `sun_path` holds, taking the shortest of the platforms this runs
on (macOS; Linux has 108) so that a name works the same everywhere.

The socket's directory counts toward it, and the `TMPDIR` fallback macOS
supplies is itself ~50 bytes, so a name well inside `MAX_NAME_LENGTH` can still
be one this cannot connect to. The client measures the assembled path and says
so, rather than letting `connect()` fail with a bare errno.
"""

_NAME_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)
"""A name becomes a file name in a shared directory, so it may not escape one."""


class UnsafeRuntimeDir(Exception):
    """The directory a session's socket lives in is reachable by someone else."""


class Session:
    """The session an invocation named, and how it named it.

    `explicit` is whether the caller typed `--session` rather than setting the
    environment variable, and it decides what happens when no such session is
    running: a typed name is an assertion, and running cold against one would
    silently lose the state the caller was counting on; an ambient one is a
    preference, and an invocation that still works when the server is down is
    the behavior that does not break a script.
    """

    __slots__ = ("name", "explicit")

    def __init__(self, name: str, *, explicit: bool) -> None:
        self.name = name
        self.explicit = explicit

    def __repr__(self) -> str:
        return f"Session({self.name!r}, explicit={self.explicit})"


def requested_session(
    argv: "Sequence[str]", environ: "Mapping[str, str]"
) -> "Session | None":
    """The session this invocation names, or None if it names none.

    A scan rather than a parse: the client cannot afford click, and the server
    is the thing that parses flags. The one ambiguity is an option *value* that
    is the literal string `--session`, which this would read as the flag -- the
    same ambiguity every argv pre-scan has, and one that fails visibly (a
    session nobody started) rather than quietly.

    The last `--session` wins, as it does for every option click parses. A
    `--session` with no value comes back with an empty name, which the client
    refuses by that flag's name.
    """
    named: "Session | None" = None
    for index, argument in enumerate(argv):
        if argument == "--":
            # everything after it is an argument, not an option
            break
        if argument.startswith(SESSION_OPTION + "="):
            named = Session(argument.split("=", 1)[1], explicit=True)
        elif argument == SESSION_OPTION:
            named = Session(
                argv[index + 1] if index + 1 < len(argv) else "", explicit=True
            )
    if named is not None:
        return named
    ambient = environ.get(SESSION_ENV_VAR)
    return None if not ambient else Session(ambient, explicit=False)


def without_session_option(argv: "Sequence[str]") -> "list[str]":
    """`argv` with the client's own flag taken off, for the server to parse."""
    remaining: "list[str]" = []
    skip_next = False
    for index, argument in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if argument == "--":
            return [*remaining, *argv[index:]]
        if argument.startswith(SESSION_OPTION + "="):
            continue
        if argument == SESSION_OPTION:
            skip_next = index + 1 < len(argv)
            continue
        remaining.append(argument)
    return remaining


def is_valid_name(name: str) -> bool:
    return (
        bool(name)
        and len(name) <= MAX_NAME_LENGTH
        and not (set(name) - _NAME_CHARACTERS)
    )


def runtime_dir(environ: "Mapping[str, str]") -> str:
    """The directory a session's socket lives in, 0700 and owned by this user.

    `XDG_RUNTIME_DIR` where the system provides one, and a per-uid directory
    under `TMPDIR` where it does not -- which is macOS, WSL2 without systemd,
    and a container, so the fallback is the common path rather than the exotic
    one. The derivation is stdlib so that both halves of a session share this
    function and cannot disagree about where the socket is.
    """
    xdg_runtime_dir = environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime_dir:
        return os.path.join(xdg_runtime_dir, "hsql")
    return os.path.join(environ.get("TMPDIR") or "/tmp", f"hsql-{os.getuid()}")


def socket_path(name: str, environ: "Mapping[str, str]") -> str:
    """Where the session called `name` listens. Derived, never configurable.

    A caller-named socket file is the obvious escape hatch and the obvious way
    to end up with one on a world-writable directory, or on a filesystem that
    cannot host a socket at all.
    """
    return os.path.join(runtime_dir(environ), f"{name}.sock")


def check_runtime_dir(path: str) -> None:
    """Refuse a socket directory that anyone but this user could reach.

    What a client sends is argv, the cwd and piped stdin, and argv can carry a
    connection string with a password in it. Under `XDG_RUNTIME_DIR` the system
    guarantees the directory; under the `TMPDIR` fallback nothing does, and one
    another user created first would receive all of it. `os.mkdir(mode=0o700)`
    does nothing to a directory that already exists, so the server checks this
    too rather than trusting its own mkdir.

    `lstat` rather than `stat`, so a symlink fails the directory test below
    instead of being followed: an attacker who reaches the fallback path first
    can point a link at a directory the user does own privately, pass a
    following check, and repoint it before the connect. A real directory in a
    sticky `/tmp` cannot be renamed or replaced by another user, and neither
    location has a reason to be a link.

    Raises OSError if the directory cannot be read, and UnsafeRuntimeDir if it
    is not a directory this user owns privately.
    """
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafeRuntimeDir(f"{path} is not a directory")
    if info.st_uid != os.getuid():
        raise UnsafeRuntimeDir(f"{path} belongs to uid {info.st_uid}, not to you")
    if info.st_mode & 0o077:
        raise UnsafeRuntimeDir(
            f"{path} is mode {stat.S_IMODE(info.st_mode):03o}, "
            "so other users can reach what it holds"
        )
