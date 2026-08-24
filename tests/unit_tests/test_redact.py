"""`harlequin.redact`: the promise that a secret does not reach the output.

Asserted negatively wherever it can be -- what matters is not that a mask
appears but that the value does not, and only one of those two is a promise.
The DSN cases are written the way people write them, because the helper exists
for connection strings that were typed by hand.
"""

from __future__ import annotations

import pytest

from harlequin.options import FlagOption, ListOption, TextOption
from harlequin.redact import (
    REDACTED,
    hide,
    redact_conn_str,
    redact_profile,
    redact_text,
    secrets_in,
)

SECRET = "hunter2-and-then-some"


@pytest.fixture
def declared() -> list[TextOption]:
    """An adapter that declares one of its two options secret."""
    return [
        TextOption(name="host", description="The host."),
        TextOption(name="md_token", description="A service token.", secret=True),
    ]


# --- a profile ---------------------------------------------------------------


def test_a_declared_secret_is_masked(declared: list[TextOption]) -> None:
    profile = {"adapter": "duckdb", "host": "warehouse", "md_token": SECRET}
    assert redact_profile(profile, declared) == {
        "adapter": "duckdb",
        # not everything: a report a reader cannot use is not safer, it is just
        # unread, and the host is the half that answers their question
        "host": "warehouse",
        "md_token": REDACTED,
    }


def test_redacting_a_profile_leaves_the_original_alone(
    declared: list[TextOption],
) -> None:
    """The caller is reporting on a profile a run may still be connecting with,
    and asterisks are not a token."""
    profile = {"md_token": SECRET}
    assert redact_profile(profile, declared)["md_token"] == REDACTED
    assert profile["md_token"] == SECRET


def test_a_key_named_like_a_secret_is_masked_without_a_declaration() -> None:
    """The backstop, and the case that is nearly every case today.

    Every adapter's options predate `secret=`, so a `password` key whose
    adapter has not adopted the flag -- or whose adapter is not installed, or
    would not import -- has nothing declaring it. Redacting a key named like a
    password that is not one costs a reader nothing they cannot read out of
    their own file.
    """
    profile = {"password": SECRET, "api_key": SECRET, "port": 5432}
    assert redact_profile(profile) == {
        "password": REDACTED,
        "api_key": REDACTED,
        "port": 5432,
    }


def test_an_option_declared_by_name_beats_nothing_at_all(
    declared: list[TextOption],
) -> None:
    """A key the adapter does declare, and does not call secret, is printed.

    `md_token` is masked because it says so, `host` is not because it does not,
    and neither answer comes from the key's spelling.
    """
    redacted = redact_profile({"host": "db", "md_token": SECRET}, declared)
    assert redacted["host"] == "db"
    assert redacted["md_token"] == REDACTED


def test_a_repeatable_secret_is_masked_item_by_item() -> None:
    """The shape a profile has is the shape the report has: a reader can still
    see how many were set."""
    option = ListOption(name="token", description="Tokens.", secret=True)
    assert redact_profile({"token": ["one", "two"]}, [option]) == {
        "token": [REDACTED, REDACTED]
    }


def test_a_secret_that_is_not_a_string_is_still_masked() -> None:
    option = FlagOption(name="secret_mode", description="x", secret=True)
    assert redact_profile({"secret_mode": True}, [option]) == {"secret_mode": REDACTED}


def test_a_profile_with_nothing_to_hide_is_unchanged() -> None:
    profile = {"adapter": "sqlite", "conn_str": [":memory:"], "read_only": True}
    assert redact_profile(profile) == profile


# --- a connection string -----------------------------------------------------


@pytest.mark.parametrize(
    "conn_str,expected",
    [
        (
            "postgresql://reporting:hunter2@warehouse:5432/analytics",
            f"postgresql://reporting:{REDACTED}@warehouse:5432/analytics",
        ),
        (
            "postgresql://:hunter2@warehouse/analytics",
            f"postgresql://:{REDACTED}@warehouse/analytics",
        ),
        (
            "postgresql://warehouse:5432/analytics?password=hunter2&sslmode=require",
            f"postgresql://warehouse:5432/analytics?password={REDACTED}"
            "&sslmode=require",
        ),
        (
            "host=warehouse password=hunter2 user=reporting",
            f"host=warehouse password={REDACTED} user=reporting",
        ),
        (
            "host=warehouse password='hunter 2' user=reporting",
            f"host=warehouse password='{REDACTED}' user=reporting",
        ),
        (
            "md:my_db?motherduck_token=hunter2",
            f"md:my_db?motherduck_token={REDACTED}",
        ),
        ("DRIVER={x};UID=me;PWD=hunter2", f"DRIVER={{x}};UID=me;PWD={REDACTED}"),
    ],
    ids=[
        "url userinfo",
        "url userinfo, no user",
        "url query string",
        "libpq pairs",
        "libpq pairs, quoted",
        "motherduck",
        "odbc",
    ],
)
def test_a_dsn_keeps_everything_but_the_credential(
    conn_str: str, expected: str
) -> None:
    """A connection string is positional, so no option describes it -- and
    [#354](https://github.com/tconbeer/harlequin/issues/354) is evidence people
    put passwords in one."""
    assert redact_conn_str([conn_str]) == [expected]
    assert "hunter2" not in redact_conn_str([conn_str])[0]


@pytest.mark.parametrize(
    "conn_str",
    [
        ":memory:",
        "./warehouse.duckdb",
        "postgresql://reporting@warehouse:5432/analytics",
        "host=warehouse user=reporting sslmode=require",
        "/tmp/a:b@c.db",
    ],
)
def test_a_dsn_with_no_credential_in_it_is_left_alone(conn_str: str) -> None:
    """The half that would make this unusable: masking a path, or a user, or a
    host, in a helper a reader reaches for to see what they connected to."""
    assert redact_conn_str([conn_str]) == [conn_str]


def test_a_password_that_spells_the_host_takes_only_itself() -> None:
    """Masked where it stands rather than substituted, so a short password does
    not take the rest of the DSN with it."""
    assert redact_conn_str(["postgresql://me:warehouse@warehouse/db"]) == [
        f"postgresql://me:{REDACTED}@warehouse/db"
    ]


def test_every_connection_string_is_redacted() -> None:
    """An adapter may take several, and `conn_str` is a list in a profile."""
    assert redact_conn_str(["a://u:one@h", ":memory:", "b://u:two@h"]) == [
        f"a://u:{REDACTED}@h",
        ":memory:",
        f"b://u:{REDACTED}@h",
    ]


def test_a_profile_redacts_its_connection_strings(declared: list[TextOption]) -> None:
    redacted = redact_profile(
        {"conn_str": ["postgresql://me:hunter2@h/db"], "md_token": SECRET}, declared
    )
    assert redacted["conn_str"] == [f"postgresql://me:{REDACTED}@h/db"]
    assert redacted["md_token"] == REDACTED


def test_a_profile_that_wrote_one_connection_string_as_a_string() -> None:
    """TOML has a string and an array, and a profile may say either."""
    assert redact_profile({"conn_str": "postgresql://me:hunter2@h/db"}) == {
        "conn_str": [f"postgresql://me:{REDACTED}@h/db"]
    }


# --- the backstop ------------------------------------------------------------


def test_text_hides_every_secret_it_was_given() -> None:
    """The channel nothing here shapes in advance: a driver exception that
    quotes back the DSN it was handed."""
    message = f"FATAL: password authentication failed for {SECRET}"
    assert redact_text(message, {SECRET}) == (
        f"FATAL: password authentication failed for {REDACTED}"
    )


def test_text_hides_a_secret_that_contains_another() -> None:
    """Longest first, so the shorter mask does not end up embedded in the
    longer secret's remains."""
    assert redact_text("abcdefgh", {"abcd", "abcdefgh"}) == REDACTED


def test_text_will_not_mangle_a_message_over_a_three_letter_secret() -> None:
    """A secret short enough to appear inside ordinary words is one this cannot
    hide in prose without destroying the message. It is still masked wherever
    it is printed as itself, which is what `redact_profile` is for."""
    assert redact_text("the cat sat", {"at"}) == "the cat sat"


def test_text_with_nothing_to_hide_is_unchanged() -> None:
    assert redact_text("could not connect", set()) == "could not connect"


# --- what the backstop is given ----------------------------------------------


def test_the_secrets_of_a_profile_are_its_declared_values_and_its_dsn(
    declared: list[TextOption],
) -> None:
    assert secrets_in(
        {
            "host": "warehouse",
            "md_token": SECRET,
            "conn_str": ["postgresql://me:hunter2@h/db"],
        },
        declared,
    ) == {SECRET, "hunter2"}


def test_the_secrets_of_a_profile_are_strings(declared: list[TextOption]) -> None:
    """A port or a flag has no literal to look for in a message, and hunting
    for `True` in prose would redact every sentence containing it."""
    option = FlagOption(name="secret_mode", description="x", secret=True)
    assert secrets_in({"secret_mode": True, "port": 5432}, [option]) == set()


def test_a_profile_with_no_secrets_yields_none() -> None:
    assert secrets_in({"conn_str": [":memory:"], "read_only": True}) == set()


# --- what the process has been told ------------------------------------------


def test_hiding_secrets_accumulates() -> None:
    """Two calls, both remembered, and both applied by default.

    Callers hide a value as soon as they have one rather than collecting every
    secret first and handing them over in one go -- which would mean each of
    them keeping a set of its own, and one of them forgetting to.
    """
    hide(["first-secret-value"])
    hide(["second-secret-value"])
    assert redact_text("saw first-secret-value and second-secret-value") == (
        f"saw {REDACTED} and {REDACTED}"
    )


def test_hiding_nothing_hides_nothing() -> None:
    """An option set to an empty string is not a secret, and substituting for
    it would replace the gap between every two characters."""
    hide([""])
    assert redact_text("nothing to hide here") == "nothing to hide here"


def test_text_given_secrets_uses_those_instead() -> None:
    """The default is a convenience, not the only way in: a caller that holds
    a secret this process was never told still gets it masked."""
    assert redact_text("saw fifth-secret-value", {"fifth-secret-value"}) == (
        f"saw {REDACTED}"
    )
