"""Hiding a secret from output that would otherwise have printed it.

An adapter declares which of its options hold one (`AbstractOption.secret`),
because core cannot enumerate what every driver considers sensitive, and this
is the layer every consumer that reports on a profile routes through. Two
things the declaration cannot reach: a connection string, which is positional
and so described by no option, and a driver exception that echoes one, which
never passes through the option layer at all -- hence `redact_conn_str()` and
`redact_text()` beside `redact_profile()`
([#667](https://github.com/tconbeer/harlequin/issues/667),
[#354](https://github.com/tconbeer/harlequin/issues/354)).

A key *named* like a password is masked too, declared or not: nearly every
adapter's options predate `secret=`, an adapter that is not installed declares
nothing at all, and over-redaction is the safe direction.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from harlequin.config import Profile, sluggify_option_name

if TYPE_CHECKING:
    from harlequin.options import AbstractOption

REDACTED = "********"
"""What a hidden value prints as. Fixed-width and value-independent: a mask
that varied with the secret's length would leak the length."""

_SECRET_NAME = re.compile(
    r"password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key",
    re.IGNORECASE,
)
"""The names a secret is written under, in a profile's keys and in a DSN's."""

_URI_PASSWORD = re.compile(r"://[^/?#@\s]*?:([^/?#@\s]+)@")
"""The password in `scheme://user:password@host`, and not the user beside it."""

_DSN_PASSWORD = re.compile(
    r"[\w.\-]*(?:" + _SECRET_NAME.pattern + r")[\w.\-]*"
    r"\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([^&;\s]*))",
    re.IGNORECASE,
)
"""`password=hunter2`, in a URL's query string or in libpq's space-separated
pairs, quoted or bare."""

_TOO_SHORT_TO_HIDE = 4
"""Below this, substituting a secret into prose would mangle the message."""

_HIDDEN: set[str] = set()
"""Every secret this process has been handed, by however many callers."""


def hide(secrets: Iterable[str]) -> None:
    """Add values that must never be printed. Call it as soon as you have one."""
    _HIDDEN.update(secret for secret in secrets if secret)


def hidden() -> frozenset[str]:
    """Everything `hide()` has been told, for a caller about to print."""
    return frozenset(_HIDDEN)


def redact_profile(
    profile: Profile, options: Sequence[AbstractOption] | None = None
) -> Profile:
    """One profile with its secrets masked, ready to print.

    A copy: the caller is reporting on a profile a run may still be using, and
    a redaction that mutated it would hand the adapter asterisks to connect
    with.

    `options` is what the adapter declares, where the caller has it. None means
    the caller could not ask -- an adapter that is not installed, or will not
    import -- in which case a key is hidden on its name alone.
    """
    declared = _declarations(options)
    return {
        key: (
            redact_conn_str(value if isinstance(value, (list, tuple)) else [value])
            if key == "conn_str"
            else _masked(value)
            if _is_secret(key, declared)
            else value
        )
        for key, value in profile.items()
    }


def redact_conn_str(conn_str: Sequence[str]) -> list[str]:
    """Every connection string with the credentials in it masked.

    The rest of the DSN survives, which is the point: `postgres://
    reporting:********@warehouse:5432/analytics` still answers the question a
    reader has, and the password it no longer carries was never part of the
    answer.
    """
    return [_mask_spans(item) for item in conn_str]


def redact_text(text: str, secrets: Iterable[str]) -> str:
    """`text` with every one of `secrets` replaced, wherever it appears.

    The backstop for output this module never shaped: a driver exception that
    quotes the DSN it was handed, or an error message that names the value it
    could not use. Longest first, so a secret that contains another is masked
    whole rather than leaving the shorter one's mask embedded in it.
    """
    for secret in sorted(
        {s for s in secrets if len(s) >= _TOO_SHORT_TO_HIDE}, key=len, reverse=True
    ):
        text = text.replace(secret, REDACTED)
    return text


def secrets_in(
    profile: Mapping[str, Any], options: Sequence[AbstractOption] | None = None
) -> set[str]:
    """The literal values in this profile that must not be printed.

    What `redact_text` needs and cannot work out for itself. Both sources are
    here because both are in the profile: an option the adapter declared
    secret, and a credential inside a `conn_str`.

    Strings only. A secret that is not one -- a port, a flag -- has no literal
    to look for in a message, and hunting for `True` in prose would redact
    every sentence containing it.
    """
    declared = _declarations(options)
    found: set[str] = set()
    for key, value in profile.items():
        if key == "conn_str":
            items = value if isinstance(value, (list, tuple)) else [value]
            for item in items:
                if isinstance(item, str):
                    found.update(text for _, _, text in _secret_spans(item))
        elif _is_secret(key, declared):
            items = value if isinstance(value, (list, tuple)) else [value]
            found.update(item for item in items if isinstance(item, str) and item)
    return found


def _declarations(
    options: Sequence[AbstractOption] | None,
) -> dict[str, AbstractOption]:
    """An adapter's options, keyed the way a profile spells them.

    `--read-only` is `read_only` in a config file, and this is asked with a
    profile's key in hand.
    """
    return {sluggify_option_name(option.name): option for option in options or []}


def _is_secret(key: str, declared: Mapping[str, AbstractOption]) -> bool:
    """Whether a profile's key holds something that must not be printed.

    The declaration first, because it is the answer that scales; the name as a
    backstop, because almost nothing declares one yet. `getattr` rather than
    the attribute, for the third-party subclass that predates `secret`.
    """
    option = declared.get(key)
    if option is not None and getattr(option, "secret", False):
        return True
    return _SECRET_NAME.search(key) is not None


def _masked(value: Any) -> Any:
    """A secret value as it prints: a mask, or a list of them.

    A list rather than one mask for a repeatable option, so that a reader can
    still see how many were set -- and so the shape a profile has is the shape
    this reports.
    """
    if isinstance(value, (list, tuple)):
        return [REDACTED for _ in value]
    return REDACTED


def _secret_spans(conn_str: str) -> list[tuple[int, int, str]]:
    """Every `(start, end, value)` in one DSN that is a credential."""
    spans: list[tuple[int, int, str]] = []
    for pattern in (_URI_PASSWORD, _DSN_PASSWORD):
        for match in pattern.finditer(conn_str):
            # one group per alternative the value could have been written as,
            # and exactly one of them matched
            index = next(
                (i for i in range(1, (match.re.groups or 0) + 1) if match.group(i)),
                None,
            )
            if index is not None:
                spans.append((match.start(index), match.end(index), match.group(index)))
    return spans


def _mask_spans(conn_str: str) -> str:
    """One DSN with each credential in it replaced where it stands.

    By position rather than by substitution, so a password that happens to
    spell the host name does not take the host with it.
    """
    masked = conn_str
    for start, end, _ in sorted(_secret_spans(conn_str), reverse=True):
        masked = masked[:start] + REDACTED + masked[end:]
    return masked
