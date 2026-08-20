"""Hiding a secret from output that would otherwise have printed it.

Everything that reports on a profile -- `hsql --info`, `hsql --config show`,
the IDE's debug screen -- prints values a user wrote in a config file, and one
of those values is a password often enough that
[#667](https://github.com/tconbeer/harlequin/issues/667) is open about it. This
is the layer those consumers route through, and nothing else: a helper here
hides a value, and never decides on its own which values a database driver
considers sensitive.

**What to hide is a declaration, not a list.** `AbstractOption.secret` is where
an adapter says so, once, and every consumer gets it free -- core cannot
enumerate `--service-account-key`, `--token`, `--tls-key` and whatever the next
adapter invents. Two things the declaration cannot reach, and they are why this
module has three functions instead of one:

- **A connection string is positional**, so no option describes
  `postgres://user:pw@host/db`. `redact_conn_str` reads the DSN shapes people
  actually write ([#354](https://github.com/tconbeer/harlequin/issues/354) is
  evidence they write passwords into them), which does take a list of key
  names -- the DSN's keys, not any adapter's options.
- **A driver exception that echoes a DSN** never passes through the option
  layer at all, so `redact_text` is the backstop: hand it the literal values
  `secrets_in()` found and it hides them wherever they turn up.

`_SECRET_NAME` is also applied to a profile's own keys, which is the one place
this second-guesses an adapter. It has to: as of this writing every adapter's
options predate `secret=`, so a `password` key declared by an adapter that has
not adopted the flag yet would print in full -- and so would every key of an
adapter that is not installed on the machine reading the config, or will not
import on it. Redacting a key named like a secret that is not one costs a
reader nothing they cannot get from the file they wrote. Over-redaction is the
safe direction, and it is the only direction that makes "the secret appears in
no byte of the report" true today.

It is a backstop and not the mechanism, though: a caller that can ask the
adapter passes `options`, and every one of them does.
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
"""The names a value that must not be printed is written under.

Read against a profile's keys and against a DSN's, which are the same
vocabulary -- `password=` in a libpq connection string and `password = "..."`
in a `[profiles.x]` table are the same key written twice.
"""

_USERINFO = re.compile(r"://[^/?#@\s]*?:([^/?#@\s]+)@")
"""The password in `scheme://user:password@host`, and nothing else in it.

The user is a name, not a secret, and a reader troubleshooting a connection
needs it. Non-greedy up to the first `:` so a user containing no colon is not
mistaken for one.
"""

_KEYED = re.compile(
    r"[\w.\-]*(?:" + _SECRET_NAME.pattern + r")[\w.\-]*"
    r"\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([^&;\s]*))",
    re.IGNORECASE,
)
"""`password=hunter2`, wherever it is written.

One pattern covers the two DSN shapes that carry one: a URL's query string
(`?password=hunter2&sslmode=require`) and libpq's space-separated pairs
(`host=db password='hunter 2'`), which is also what ODBC and several drivers
take. The quoted alternatives are first so a quoted value containing a space or
an `&` is taken whole.
"""

_TOO_SHORT_TO_HIDE = 4
"""The floor `redact_text` will substitute below.

A three-character secret appears inside ordinary words, and replacing every
occurrence would mangle the message it was meant to make safe. Nothing is lost
where the value is printed as itself -- `redact_profile` masks a declared
secret whatever its length -- and a password this short is not one this can
protect in prose.
"""


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
    for pattern in (_USERINFO, _KEYED):
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
