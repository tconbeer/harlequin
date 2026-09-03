"""The frames a session's client and server exchange, and how they are framed.

What crosses the wire is **stdout bytes, stderr bytes and an exit code**, not
rows: the server runs the same execution core and the same writer the cold path
does, into a buffer, and the client copies that buffer to its own streams. So
the client cannot format anything, needs no pyarrow and no `--format`
knowledge, and cannot disagree with a cold invocation about a timestamp.

Output is **chunked** from the first version -- many `STDOUT` frames, then one
`EXIT` -- because that is free now and impossible to retrofit without a
protocol version bump.

Stdlib only, and cheap, for the reason `client.py` gives: `json` alone would be
a third of what a warm invocation is meant to cost, so the encoding here is
lengths and bytes.
"""

from __future__ import annotations

import os
import struct

TYPE_CHECKING = False
"""`typing` costs ~10ms, and every annotation here is a string (PEP 563)."""

if TYPE_CHECKING:
    import socket
    from typing import Mapping, Sequence

VERSION = "2.13.0"
"""The release this protocol belongs to; a client refuses a server on another.

hsql's output bytes are its API, and "frozen" has always meant "across the
releases we intend" -- a server from a different release serving bytes a caller
attributes to the installed version is a bug nobody diagnoses quickly. So the
handshake compares releases rather than a protocol number somebody has to
remember to bump. `scripts/bump_versions.py` writes this at release time, and
`tests/unit_tests/test_hsql_session.py` pins it to the installed version.
"""

HELLO = 1
"""Server to client, first: the server's version, so a skewed client stops."""

REQUEST = 2
"""Client to server: argv, cwd, the environment it forwards, and stdin."""

STDOUT = 3
"""Server to client, repeatable: bytes for the client's stdout."""

STDERR = 4
"""Server to client, repeatable: bytes for the client's stderr."""

EXIT = 5
"""Server to client, last: the code the client exits with."""

# Frame kinds are wire values: later ones append rather than renumbering.

FORWARDED_ENV_VARS = ("TERM", "NO_COLOR")
"""The whole of the environment a request carries.

`--color auto` reads both, and the server cannot know either. Not the caller's
whole environment: the server has its own, an adapter option's `envvar`
resolves against that one, and shipping a caller's environment across a socket
is a credential leak with no upside.
"""

HEADER = struct.Struct("!BI")
"""One frame: a kind, a payload length, and that many bytes."""

MAX_PAYLOAD = 64 * 1024 * 1024
"""A frame nobody would send, so a bad length cannot become an allocation."""

CHUNK_SIZE = 64 * 1024
"""How much of a result the server puts in one `STDOUT` frame."""

_LENGTH = struct.Struct("!I")
"""How a sequence writes its count, and each of its items its length."""


class ProtocolError(Exception):
    """The peer sent something this cannot read."""


class Request:
    """One invocation, as the server receives it.

    The cwd is here because config discovery is cwd-dependent: a client's `-P`
    and `--config-path` resolve against *the client's* directory, or a caller
    running in a project would silently get the server's `pyproject.toml`.
    """

    __slots__ = ("argv", "cwd", "environ", "stdin")

    def __init__(
        self,
        argv: "Sequence[str]",
        cwd: str,
        environ: "Mapping[str, str]",
        stdin: "bytes | None",
    ) -> None:
        self.argv = list(argv)
        self.cwd = cwd
        self.environ = dict(environ)
        self.stdin = stdin


def pack_strings(items: "Sequence[bytes]") -> bytes:
    """A count, then each item's length and bytes. The one primitive here."""
    parts = [_LENGTH.pack(len(items))]
    for item in items:
        parts.append(_LENGTH.pack(len(item)))
        parts.append(item)
    return b"".join(parts)


def unpack_strings(payload: bytes) -> "list[bytes]":
    if len(payload) < _LENGTH.size:
        raise ProtocolError("truncated sequence")
    (count,) = _LENGTH.unpack_from(payload, 0)
    offset = _LENGTH.size
    items = []
    for _ in range(count):
        if offset + _LENGTH.size > len(payload):
            raise ProtocolError("truncated sequence")
        (length,) = _LENGTH.unpack_from(payload, offset)
        offset += _LENGTH.size
        if offset + length > len(payload):
            raise ProtocolError("truncated sequence")
        items.append(payload[offset : offset + length])
        offset += length
    if offset != len(payload):
        raise ProtocolError("trailing bytes after a sequence")
    return items


def pack_request(
    *,
    argv: "Sequence[str]",
    cwd: str,
    environ: "Mapping[str, str]",
    stdin: "bytes | None",
) -> bytes:
    """Four sections, each a sequence: argv, cwd, environment, stdin.

    Text goes through `os.fsencode`, which is what argv and paths already are:
    a file name the filesystem accepts and UTF-8 does not survives the round
    trip rather than failing at the socket.
    """
    environment = []
    for key, value in environ.items():
        environment.append(os.fsencode(key))
        environment.append(os.fsencode(value))
    return pack_strings(
        [
            pack_strings([os.fsencode(argument) for argument in argv]),
            pack_strings([os.fsencode(cwd)]),
            pack_strings(environment),
            pack_strings([] if stdin is None else [stdin]),
        ]
    )


def unpack_request(payload: bytes) -> Request:
    sections = unpack_strings(payload)
    if len(sections) != 4:
        raise ProtocolError(f"a request has four sections, not {len(sections)}")
    raw_argv, raw_cwd, raw_environ, raw_stdin = (
        unpack_strings(section) for section in sections
    )
    if len(raw_cwd) != 1:
        raise ProtocolError("a request carries one working directory")
    if len(raw_environ) % 2:
        raise ProtocolError("a request's environment is key/value pairs")
    if len(raw_stdin) > 1:
        raise ProtocolError("a request carries at most one stdin")
    return Request(
        argv=[os.fsdecode(argument) for argument in raw_argv],
        cwd=os.fsdecode(raw_cwd[0]),
        environ={
            os.fsdecode(raw_environ[index]): os.fsdecode(raw_environ[index + 1])
            for index in range(0, len(raw_environ), 2)
        },
        stdin=raw_stdin[0] if raw_stdin else None,
    )


def forwarded_environ(environ: "Mapping[str, str]") -> "dict[str, str]":
    """The variables a request carries, and only the ones that are set.

    `NO_COLOR` is read for its presence, so an unset one has to arrive absent
    rather than empty.
    """
    return {key: environ[key] for key in FORWARDED_ENV_VARS if key in environ}


def send_frame(sock: "socket.socket", kind: int, payload: bytes = b"") -> None:
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(f"a {len(payload)}-byte frame is too large to send")
    sock.sendall(HEADER.pack(kind, len(payload)) + payload)


def recv_frame(sock: "socket.socket") -> "tuple[int, bytes] | None":
    """The next frame, or None at a clean end of stream."""
    header = _recv_exactly(sock, HEADER.size)
    if header is None:
        return None
    kind, length = HEADER.unpack(header)
    if length > MAX_PAYLOAD:
        raise ProtocolError(f"a {length}-byte frame is too large to read")
    if not length:
        return kind, b""
    payload = _recv_exactly(sock, length)
    if payload is None:
        raise ProtocolError("the connection ended mid-frame")
    return kind, payload


def _recv_exactly(sock: "socket.socket", length: int) -> "bytes | None":
    """`length` bytes, or None if the peer closed before sending any of them."""
    chunks = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            if remaining == length:
                return None
            raise ProtocolError("the connection ended mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
