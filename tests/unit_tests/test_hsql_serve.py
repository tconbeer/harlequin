"""The half of a warm session that holds the connection: `hsql --serve`.

What is asserted here is the server's half of the contract: which options each
role takes, that requests run one at a time on one connection, that the
session's state is what `--session-reset` clears, and the ways a session comes
down. Byte-equivalence with the cold path is `test_hsql_equivalence.py`'s.

Servers run in a fresh interpreter, because a server swaps the process's
streams, environment and working directory for every request; the command's
own refusals run in process, through the same `Served` the server hands it.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence, cast

import click
import pytest
from click.testing import CliRunner, Result

from harlequin.config import TUI_ONLY_KEYS
from harlequin.exception import HarlequinConnectionError
from harlequin.hsql import protocol, server
from harlequin.hsql.cli import (
    CONFIG_OPTIONS,
    CONNECTION_OPTIONS,
    PER_REQUEST_OPTIONS,
    ROLE_OPTIONS,
    SERVER_OPTIONS,
    bare_command,
    build_cli,
    connection_option_names,
    connection_options_in,
)
from harlequin.hsql.diagnostics import ExitCode
from harlequin.hsql.server import Served, Server
from harlequin.hsql.timeout import Deadline, TimedOut
from harlequin.plugins import load_adapter
from harlequin.transaction_mode import HarlequinTransactionMode
from tests.hsql_sessions import HsqlSubprocess, ServeSession, WarmSession

needs_unix_sockets = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"), reason="hsql sessions are POSIX-only"
)

Hsql = Callable[..., Result]

UNFINISHABLE = "select count(*) from range(200000000000)"
"""A query no runner finishes inside a test's timeout.

`range()` streams, so this counts rows rather than materializing them, and
duckdb checks for an interrupt between chunks. Large enough that the fastest
runner cannot beat a tenth of a second: at 2e9 an arm64 macOS runner did, and
the timeout test got a result set where it wanted none.
"""

SESSION_INIT_PATH = Path("boot.sql")
"""The `--init-path` the in-process session recorded.

A `Path`, because that is what a `PathOption` resolves to and rendering one
is what `_shown()` exists for -- and a bare name, so that asserting on how it
prints spells no separator that is right on only one platform.
"""


@pytest.fixture
def hsql(no_discovered_config: None) -> Hsql:
    """Invoke hsql in process, cold, or served when `obj` names a session."""
    runner = CliRunner()

    def _run(*args: str, **kwargs: Any) -> Result:
        argv = [str(arg) for arg in args]
        return runner.invoke(build_cli(argv), argv, catch_exceptions=False, **kwargs)

    return _run


@pytest.fixture
def duck() -> list[str]:
    return ["-a", "duckdb", "--no-init", ":memory:"]


@pytest.fixture
def in_process_server(duckdb_adapter: Any) -> Server:
    """A session with a real DuckDB connection, and no socket.

    The identity is what `--serve` records when it connects: it is what a
    served request's connection options are compared against, so a session
    without one would refuse every option rather than the ones that differ.
    """
    adapter = duckdb_adapter([":memory:"], no_init=True)
    return Server(
        "inproc",
        adapter="duckdb",
        connection=adapter.connect(),
        reconnect=adapter.connect,
        # what `hsql --serve inproc -P prod` records for a profile that says
        # duckdb, `:memory:`, `no_init` and `read_only = false`
        identity={
            "conn_str": (":memory:",),
            "read_only": False,
            "no_init": True,
            "init_path": SESSION_INIT_PATH,
        },
    )


def served_by(server: Server, *, stdin: bytes | None = None) -> Served:
    return Served(
        server,
        protocol.Request(argv=[], cwd=os.getcwd(), environ={}, stdin=stdin),
    )


# --- the partition -----------------------------------------------------------


def test_every_option_is_in_exactly_one_group() -> None:
    """The rule that defines the two roles: `--serve` refuses the per-request
    group and a served request refuses the server's, so an option in neither
    or in both would be one nobody refuses -- or one both do."""
    declared = {param.name for param in bare_command().params if param.name}
    groups = [
        CONNECTION_OPTIONS,
        CONFIG_OPTIONS,
        PER_REQUEST_OPTIONS,
        SERVER_OPTIONS,
        ROLE_OPTIONS,
    ]
    assert declared == set().union(*groups)
    for first in groups:
        for second in groups:
            if first is not second:
                assert not first & second


def test_a_session_records_the_connection_time_group_and_nothing_else() -> None:
    """The identity a session compares a request against is the partition's
    first group, so an option added to it is compared without anything being
    added here."""
    values = {param.name: None for param in bare_command().params if param.name}
    assert (
        set(connection_options_in(values, connection_option_names(None)))
        == CONNECTION_OPTIONS
    )


def test_a_key_the_command_does_not_declare_describes_no_connection() -> None:
    """The IDE's own profile keys, and a misspelling in a profile: neither is
    hsql's nor an adapter's, so neither is compared against a session."""
    duckdb = load_adapter("duckdb").ADAPTER_OPTIONS
    names = connection_option_names(duckdb)
    assert not names & set(TUI_ONLY_KEYS)
    assert "reed_only" not in names
    assert "read_only" in names and "md_token" in names


def test_an_adapters_options_are_connection_options(hsql: Hsql) -> None:
    """The command declares none of them, so they are the group by default."""
    res = hsql("--serve", "prod", "-a", "duckdb", "--no-init", "--csv")
    assert res.exit_code == ExitCode.USAGE
    # `--csv` is refused, and `--no-init` was not -- it came first on the line
    assert "--csv is a per-request option" in res.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["-c", "select 1"],
        ["-f", "script.sql"],
        ["--format", "csv"],
        ["--limit", "10"],
        ["--timeout", "5"],
        ["--catalog"],
        ["--info"],
        ["-o", "out.csv"],
        ["--stats"],
    ],
)
def test_serve_refuses_per_request_options(hsql: Hsql, args: list[str]) -> None:
    """The feature's most likely first mistake, so the message names the right
    invocation rather than only the wrong one."""
    res = hsql("--serve", "prod", *args)
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "is a per-request option, and --serve takes none" in res.stderr
    assert "hsql --serve prod" in res.stderr
    assert "hsql --session prod" in res.stderr


def test_serve_and_session_are_two_roles(hsql: Hsql) -> None:
    res = hsql("--serve", "a", "--session", "b")
    assert res.exit_code == ExitCode.USAGE
    assert "--serve starts a session and --session sends to one" in res.stderr


def test_serve_does_not_reset(hsql: Hsql) -> None:
    """--session-reset is a client operation -- it asks a running session --
    so on --serve it is refused as the per-request option it is."""
    res = hsql("--serve", "a", "--session-reset")
    assert res.exit_code == ExitCode.USAGE
    assert "is a per-request option, and --serve takes none" in res.stderr


@pytest.mark.parametrize(
    "name,reason",
    [
        # a flag with no value is the caller's typo on every platform, so it is
        # answered before the platform is; the rest reach the name check only
        # where a session could have run
        ("", "needs a name"),
        pytest.param("bad name", "is not a session name", marks=needs_unix_sockets),
        pytest.param("../etc", "is not a session name", marks=needs_unix_sockets),
        pytest.param("x" * 65, "is not a session name", marks=needs_unix_sockets),
    ],
)
def test_serve_refuses_a_name_no_client_could_reach(
    hsql: Hsql, name: str, reason: str
) -> None:
    """Asked with the client's own check, and before a connection is paid for."""
    res = hsql("--serve", name, "-a", "duckdb", ":memory:")
    assert res.exit_code == ExitCode.USAGE
    assert reason in res.stderr


def test_queue_timeout_belongs_to_serve(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "--queue-timeout", "2", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert "--queue-timeout is a --serve option" in res.stderr


def test_session_reset_needs_a_session(hsql: Hsql) -> None:
    """It reconnects a running session, so with none named there is nothing to
    do -- and it attaches no adapter options, so it takes no connection args."""
    res = hsql("--session-reset")
    assert res.exit_code == ExitCode.USAGE
    assert "--session-reset reconnects a running session" in res.stderr
    assert "HSQL_SESSION" in res.stderr


def test_session_status_needs_a_session(hsql: Hsql) -> None:
    """The client answers it off a frame before click exists, so reaching the
    parser means no session did."""
    res = hsql("--session-status")
    assert res.exit_code == ExitCode.USAGE
    assert "--session-status reports on a running session" in res.stderr
    assert "HSQL_SESSION" in res.stderr


def test_serve_does_not_report_its_own_status(hsql: Hsql) -> None:
    """It asks a running session what it is doing, which is a client
    operation, so on --serve it is refused as the per-request option it is."""
    res = hsql("--serve", "a", "--session-status")
    assert res.exit_code == ExitCode.USAGE
    assert "is a per-request option, and --serve takes none" in res.stderr


def test_a_session_flag_that_reached_the_parser_is_refused(
    hsql: Hsql, duck: list[str]
) -> None:
    """`main()` reads it before it parses anything, so only a caller that built
    the command some other way can put it here."""
    res = hsql(*duck, "--session", "prod", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert "--session prod is read before hsql parses anything else" in res.stderr


@pytest.mark.parametrize(
    "key,value",
    [("session", '"prod"'), ("serve", '"prod"'), ("session_status", "true")],
)
def test_a_profile_may_not_decide_which_process_runs_an_invocation(
    hsql: Hsql, tmp_path: Path, key: str, value: str
) -> None:
    """The `CLI_ONLY_SSH_KEYS`-shaped refusal: `session` is decided before any
    config file is read, a profile that served would turn a query into a
    daemon, and `session_status` is read off argv by the client -- so a profile
    could set it only for the runs that never reach a session."""
    path = tmp_path / "hsql.toml"
    path.write_text(f'[profiles.prod]\nadapter = "duckdb"\n{key} = {value}\n')
    res = hsql("--config-path", path, "-P", "prod", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert f"{key} decides which process runs an invocation" in res.stderr
    assert f"--{key.replace('_', '-')}" in res.stderr


def test_a_profiles_session_key_is_not_in_the_schema() -> None:
    from harlequin.config_schema import build_schema

    profile = build_schema(bare_command().params, adapters=None)["$defs"]["profile"][
        "properties"
    ]
    assert "session" not in profile
    assert "serve" not in profile
    assert "session_status" not in profile
    # while the keys a profile may set are
    assert "queue_timeout" in profile


def test_session_is_a_profile_key_the_ide_leaves_alone() -> None:
    """One profile serves both commands, and the IDE reads hsql's keys off the
    command rather than a copy, so the new ones are there without a change."""
    from harlequin.cli import hsql_profile_keys

    assert {"session", "serve", "session_reset", "queue_timeout"} <= hsql_profile_keys()


# --- a served request, in process --------------------------------------------


@pytest.mark.parametrize(
    "args,spelling,asked,has",
    [
        (["-a", "sqlite"], "--adapter", "'sqlite'", "'duckdb'"),
        (["--read-only"], "--read-only", "True", "False"),
        (["other.db"], "CONN_STR", "['other.db']", "[':memory:']"),
    ],
)
def test_a_served_request_that_asserts_a_different_connection_is_refused(
    hsql: Hsql,
    in_process_server: Server,
    args: list[str],
    spelling: str,
    asked: str,
    has: str,
) -> None:
    """A session's connection is fixed at start-up, so a request naming a
    different one exits 2.

    Both values are asserted: what the session was started with is the half a
    caller cannot see, and a message echoing the request's value back for it
    would report two identical values as differing."""
    res = hsql(*args, "-c", "select 1", obj=served_by(in_process_server))
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert (
        f"{spelling} is {asked}. The session named 'inproc' was started with "
        f"{has}, and its connection is fixed."
    ) in res.stderr
    assert "--serve NAME" in res.stderr


def test_a_refusal_names_an_adapters_own_option_and_spells_a_path_as_one(
    hsql: Hsql, in_process_server: Server, tmp_path: Path
) -> None:
    """An adapter-declared option is compared like one of hsql's own, and a
    `PathOption` resolves to a `Path` -- which a caller reads as a path and
    not as `PosixPath('/p/boot.sql')`.

    The asked value is left to click, which resolves it against wherever the
    test runs; what is pinned is that both sides reach the caller as paths
    rather than as reprs."""
    res = hsql(
        "--init-path",
        str(tmp_path / "other.sql"),
        "-c",
        "select 1",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.USAGE
    assert "--init-path is '" in res.stderr
    assert f"was started with '{SESSION_INIT_PATH}'," in res.stderr
    assert "other.sql" in res.stderr
    assert "Path(" not in res.stderr


@pytest.mark.parametrize("args", [["-a", "duckdb"], ["--no-init"], [":memory:"]])
def test_a_served_request_may_assert_the_connection_the_session_has(
    hsql: Hsql, in_process_server: Server, args: list[str]
) -> None:
    """Identical to the server's is served: the name is the caller's and the
    identity is the server's, so a request that asks for what is already there
    has asked for nothing."""
    res = hsql(*args, "-tAc", "select 1", obj=served_by(in_process_server))
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "1\n"


@pytest.mark.parametrize("args", [["--read-only"], ["--ssh-host", "bastion"]])
def test_a_served_request_is_refused_an_option_the_session_never_named(
    hsql: Hsql, duckdb_adapter: Any, args: list[str]
) -> None:
    """A session that never named a key connected with whatever its adapter
    defaults to, which core cannot enumerate -- so a request that names it is
    refused rather than quietly served."""
    adapter = duckdb_adapter([":memory:"], no_init=True)
    session = Server(
        "bare",
        adapter="duckdb",
        connection=adapter.connect(),
        reconnect=adapter.connect,
    )
    res = hsql(*args, "-c", "select 1", obj=served_by(session))
    assert res.exit_code == ExitCode.USAGE
    assert (
        "is a connection option. The session named 'bare' was started without it"
    ) in res.stderr


def test_a_refusal_does_not_print_the_secret_it_names(
    hsql: Hsql, in_process_server: Server
) -> None:
    """A connection option is exactly where a password is typed, so the
    message about one that differs is a message about a secret."""
    res = hsql(
        "postgres://ted:hunter2@warehouse:5432/analytics",
        "-c",
        "select 1",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.USAGE
    assert "hunter2" not in res.stderr
    assert "********" in res.stderr


def test_a_served_request_takes_a_profile_of_per_request_options(
    hsql: Hsql, in_process_server: Server, tmp_path: Path
) -> None:
    """A profile names where options come from rather than being one, so what
    it holds decides: nothing here says which database, so it applies -- the
    same way a `default_profile` discovered in the caller's directory does."""
    path = tmp_path / "hsql.toml"
    path.write_text('[profiles.csv-out]\nformat = "csv"\nlimit = 5\n')
    res = hsql(
        "--config-path",
        path,
        "-P",
        "csv-out",
        "-c",
        "select 1 as a",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "a\n1\n"


@pytest.mark.parametrize(
    "key,value,shown,has",
    [
        ("adapter", '"sqlite"', "'sqlite'", "'duckdb'"),
        ("read_only", "true", "True", "False"),
        ("conn_str", '["other.db"]', "['other.db']", "[':memory:']"),
    ],
)
def test_a_served_request_refuses_a_profile_that_names_another_connection(
    hsql: Hsql,
    in_process_server: Server,
    tmp_path: Path,
    key: str,
    value: str,
    shown: str,
    has: str,
) -> None:
    """A typed profile is judged by the keys it holds rather than by being one,
    so one that names a different database is refused under the key that names
    it rather than as a flag."""
    path = tmp_path / "hsql.toml"
    path.write_text(f"[profiles.prod]\n{key} = {value}\n")
    res = hsql(
        "--config-path",
        path,
        "-P",
        "prod",
        "-c",
        "select 1",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert f"the profile 'prod' sets {key}, which is {shown}." in res.stderr
    assert f"The session named 'inproc' was started with {has}," in res.stderr
    assert "--serve NAME -P prod" in res.stderr


def test_a_served_request_takes_a_profile_that_names_the_sessions_connection(
    hsql: Hsql, in_process_server: Server, tmp_path: Path
) -> None:
    """The profile the session was started with is the one a caller is most
    likely to name here, and naming it asserts nothing the session has not
    already answered."""
    path = tmp_path / "hsql.toml"
    path.write_text(
        '[profiles.prod]\nadapter = "duckdb"\nconn_str = [":memory:"]\n'
        "no_init = true\nlimit = 5\n"
    )
    res = hsql(
        "--config-path",
        path,
        "-P",
        "prod",
        "-tAc",
        "select 1",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "1\n"


def test_a_served_request_may_not_take_a_server_option_from_a_profile(
    hsql: Hsql, in_process_server: Server, tmp_path: Path
) -> None:
    """A typed profile is judged by the keys it holds on both halves of the
    rule: a server-lifetime key in one describes a server that is already up,
    whichever way the caller named it."""
    path = tmp_path / "hsql.toml"
    path.write_text("[profiles.slow]\nqueue_timeout = 30\n")
    res = hsql(
        "--config-path",
        path,
        "-P",
        "slow",
        "-c",
        "select 1",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.USAGE
    assert "the profile 'slow' sets queue_timeout, which is a --serve option" in (
        res.stderr
    )


@pytest.mark.parametrize("key", ["theme", "locale", "viewer_max_rows"])
def test_a_served_request_takes_a_profile_the_ide_also_reads(
    hsql: Hsql, in_process_server: Server, tmp_path: Path, key: str
) -> None:
    """One profile serves both commands, so a profile with a `theme` in it is
    the normal case -- and hsql drops the IDE's keys before it connects, so a
    session records none of them and none of them names a connection."""
    path = tmp_path / "hsql.toml"
    value = "10" if key == "viewer_max_rows" else '"nord"'
    path.write_text(f'[profiles.prod]\nadapter = "duckdb"\n{key} = {value}\n')
    res = hsql(
        "--config-path",
        path,
        "-P",
        "prod",
        "-tAc",
        "select 1",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "1\n"


def test_a_profile_that_names_an_option_the_session_never_did_is_refused(
    hsql: Hsql, in_process_server: Server, tmp_path: Path
) -> None:
    """The `did not ask for` refusal, worded for a profile rather than a flag."""
    path = tmp_path / "hsql.toml"
    path.write_text('[profiles.other]\nmd_token = "tok_abcdef"\n')
    res = hsql(
        "--config-path",
        path,
        "-P",
        "other",
        "-c",
        "select 1",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.USAGE
    assert (
        "the profile 'other' sets md_token, which is a connection option. The "
        "session named 'inproc' was started without it"
    ) in res.stderr
    # declared `secret=True`, so the refusal names the key and not the token
    assert "tok_abcdef" not in res.stderr


def test_a_refusal_masks_an_option_its_adapter_declared_secret(
    hsql: Hsql, duckdb_adapter: Any
) -> None:
    """`secret=` is the mechanism, and the name backstop is only the backstop:
    a differing value is masked because duckdb declared the option, not
    because the key happens to be spelled like a password."""
    adapter = duckdb_adapter([":memory:"], no_init=True)
    session = Server(
        "md",
        adapter="duckdb",
        connection=adapter.connect(),
        reconnect=adapter.connect,
        identity={"md_token": "tok_theirs"},
        options=adapter.ADAPTER_OPTIONS,
    )
    res = hsql("--md_token", "tok_mine", "-c", "select 1", obj=served_by(session))
    assert res.exit_code == ExitCode.USAGE
    assert "tok_mine" not in res.stderr
    assert "tok_theirs" not in res.stderr
    assert "--md_token is '********'" in res.stderr
    assert "was started with '********'" in res.stderr


def test_a_profile_value_of_the_wrong_shape_is_a_usage_error(
    hsql: Hsql, in_process_server: Server, tmp_path: Path
) -> None:
    """A caller who mistyped their own config is owed exit 2 naming the key,
    not a crash report saying they found a bug in Harlequin."""
    path = tmp_path / "hsql.toml"
    path.write_text("[profiles.bad]\nconn_str = 5\n")
    res = hsql(
        "--config-path",
        path,
        "-P",
        "bad",
        "-c",
        "select 1",
        obj=served_by(in_process_server),
    )
    assert res.exit_code == ExitCode.USAGE
    assert "the profile 'bad' sets conn_str, which is ['********']" in res.stderr


def test_a_served_request_may_not_type_a_server_option(
    hsql: Hsql, in_process_server: Server
) -> None:
    res = hsql(
        "--queue-timeout", "3", "-c", "select 1", obj=served_by(in_process_server)
    )
    assert res.exit_code == ExitCode.USAGE
    assert "--queue-timeout is a --serve option, and the session named 'inproc'" in (
        res.stderr
    )


def test_a_served_request_may_not_serve(hsql: Hsql, in_process_server: Server) -> None:
    res = hsql("--serve", "other", obj=served_by(in_process_server))
    assert res.exit_code == ExitCode.USAGE
    assert "pass one of them" in res.stderr


def test_a_mode_that_reads_no_database_is_not_the_sessions_business(
    hsql: Hsql, in_process_server: Server
) -> None:
    """`--info -a sqlite` narrows a document; it does not ask to reconnect."""
    res = hsql("--info", "-a", "sqlite", obj=served_by(in_process_server))
    assert res.exit_code == ExitCode.OK
    assert '"sqlite"' in res.stdout


def test_a_served_request_runs_on_the_sessions_connection(
    hsql: Hsql, in_process_server: Server
) -> None:
    served = served_by(in_process_server)
    assert hsql("-c", "create temp table t as select 1 as v", obj=served).exit_code == 0
    res = hsql("-tAc", "select v from t", obj=served)
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "1\n"


def test_a_dash_f_dash_with_no_stdin_is_refused(
    hsql: Hsql, in_process_server: Server
) -> None:
    """The client's argv scan misses a `-f` behind a boolean short an adapter
    declares, and an empty script run as if it were what was piped is the
    silent failure this turns into an exit 2."""
    res = hsql("-f", "-", obj=served_by(in_process_server, stdin=None))
    assert res.exit_code == ExitCode.USAGE
    assert "carried none" in res.stderr
    assert "--file -" in res.stderr


def test_a_dash_f_dash_with_stdin_is_allowed(
    hsql: Hsql, in_process_server: Server
) -> None:
    """The guard refuses only a `-f -` whose request carried no stdin; with one
    it reads the caller's SQL. (The bytes come through the server's own stdin
    stand-in end to end; here the CliRunner supplies them.)"""
    served = served_by(in_process_server, stdin=b"select 5 as five")
    res = hsql("-tAf", "-", obj=served, input="select 5 as five")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "5\n"


def test_session_reset_reconnects(hsql: Hsql, in_process_server: Server) -> None:
    served = served_by(in_process_server)
    hsql("-c", "create temp table t as select 1 as v", obj=served)
    res = hsql("--session-reset", obj=served)
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert "reconnected" in res.stderr
    assert hsql("-c", "select v from t", obj=served).exit_code == ExitCode.QUERY


def test_a_reset_that_cannot_reconnect_leaves_the_session_without_a_connection(
    hsql: Hsql, duckdb_adapter: Any
) -> None:
    """Exit 3 for the reset, and exit 3 for every request after it until a
    reset succeeds: a session with no connection says so rather than pretending."""
    adapter = duckdb_adapter([":memory:"], no_init=True)
    attempts: list[int] = []

    def reconnect() -> Any:
        attempts.append(1)
        if len(attempts) == 1:
            raise HarlequinConnectionError("the warehouse is down")
        return adapter.connect()

    session = Server(
        "flaky", adapter="duckdb", connection=adapter.connect(), reconnect=reconnect
    )
    served = served_by(session)
    res = hsql("--session-reset", obj=served)
    assert res.exit_code == ExitCode.CONNECTION
    assert "the warehouse is down" in res.stderr
    res = hsql("-c", "select 1", obj=served)
    assert res.exit_code == ExitCode.CONNECTION
    assert "has no connection" in res.stderr
    assert "--session-reset" in res.stderr
    assert hsql("--session-reset", obj=served).exit_code == ExitCode.OK
    assert hsql("-c", "select 1", obj=served).exit_code == ExitCode.OK


# --- a cancel that does not land ---------------------------------------------


class _FakeConnection:
    def __init__(self, label: str) -> None:
        self.label = label
        self.closed = False

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_a_deadline_a_server_gave_an_abandon_hook_ends_the_run_not_the_process() -> (
    None
):
    """Where the cold path halts the process, a deadline given an abandon hook
    calls it and raises TimedOut for the request to attribute, so the server
    lives on."""
    abandoned: list[bool] = []
    never = threading.Event()
    connection: Any = _FakeConnection("held")
    deadline = Deadline(0.01, grace=0.01, abandon=lambda: abandoned.append(True))
    with pytest.raises(TimedOut):
        deadline.run(lambda: never.wait(30), connection=connection)
    assert abandoned == [True]
    never.set()


def test_a_bug_in_hsql_is_exit_70_through_a_session_too(
    in_process_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash is the one failure a session could answer differently from a
    cold run: 70 says a bug in hsql and 1 says the database rejected the SQL,
    which is the confusion `CRASH` exists to remove. The client gets the same
    diagnostic either way, and the session goes on serving."""

    def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("simulated bug")

    monkeypatch.setattr("harlequin.hsql.cli.run", boom)
    request = protocol.Request(
        argv=["-c", "select 1"], cwd=os.getcwd(), environ={}, stdin=None
    )
    segments, code = in_process_server._run(request)
    assert code == ExitCode.CRASH
    stderr = b"".join(bytes(d) for kind, d in segments if kind == protocol.STDERR)
    assert b"hsql hit a bug in itself" in stderr
    assert not any(kind == protocol.STDOUT for kind, _ in segments)


def test_an_abandoned_connection_is_offered_to_nobody_until_a_reset() -> None:
    """A cancel that outlasts its grace leaves the connection to the thread
    still inside it, so `abandon()` marks the session unusable until a reset,
    rather than handing the next request a connection another thread holds."""
    opened: list[Any] = [_FakeConnection("first")]

    def reconnect() -> Any:
        opened.append(_FakeConnection(f"reconnect-{len(opened)}"))
        return opened[-1]

    session = Server(
        "stuck", adapter="duckdb", connection=opened[0], reconnect=reconnect
    )
    assert session.connection() is opened[0]
    session.abandon()
    with pytest.raises(HarlequinConnectionError) as raised:
        session.connection()
    assert "cancelled a query that did not stop" in raised.value.msg
    assert "--session-reset" in raised.value.msg
    # the abandoned connection is left to its thread, not closed under it
    assert not opened[0].closed  # type: ignore[attr-defined]
    session.reset()
    assert session.connection() is opened[-1]


# --- the turnstile ---------------------------------------------------------------


def test_the_turnstile_serves_in_arrival_order() -> None:
    turnstile = server.Turnstile()
    order: list[int] = []
    assert turnstile.enter(None)

    def wait_turn(number: int) -> None:
        turnstile.enter(None)
        order.append(number)
        turnstile.leave()

    threads = []
    for number in (1, 2):
        thread = threading.Thread(target=wait_turn, args=(number,))
        thread.start()
        threads.append(thread)
        # each ticket is taken before the next thread starts, so the arrival
        # order is this loop's rather than the scheduler's
        _until(lambda waiting=number: turnstile.snapshot()[1] == waiting)  # type: ignore[misc]

    turnstile.leave()
    for thread in threads:
        thread.join(5)
    assert order == [1, 2]


def _until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    """Block until `condition` holds, so a test orders threads by observation."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("the condition never held")


def test_the_turnstile_gives_up_on_a_deadline() -> None:
    turnstile = server.Turnstile()
    assert turnstile.enter(None)
    started = time.monotonic()
    assert not turnstile.enter(0.05)
    assert time.monotonic() - started < 2
    assert turnstile.snapshot() == (True, 0)
    turnstile.leave()
    assert turnstile.enter(0.05)


# --- the recorder -------------------------------------------------------------------


def test_the_recorder_keeps_the_order_the_streams_were_written_in() -> None:
    """A note between two result sets reaches the terminal between them."""
    recorder = server.Recorder(stdout_isatty=True)
    out, err = recorder.stdout(), recorder.stderr()
    out.write("one\n")
    out.buffer.write(b"two\n")
    err.write("note\n")
    out.write("three\n")
    assert [(kind, bytes(data)) for kind, data in recorder.segments] == [
        (protocol.STDOUT, b"one\ntwo\n"),
        (protocol.STDERR, b"note\n"),
        (protocol.STDOUT, b"three\n"),
    ]
    assert out.isatty() and not err.isatty()


def test_click_writes_binary_output_to_the_recorder() -> None:
    """`-o -` is `click.open_file("-", "wb")`, which has to find the buffer."""
    recorder = server.Recorder()
    stream = recorder.stdout()
    saved = sys.stdout
    sys.stdout = stream
    try:
        click.open_file("-", mode="wb").write(b"bytes\n")
        click.echo("text")
    finally:
        sys.stdout = saved
    assert bytes(recorder.segments[0][1]) == b"bytes\ntext\n"


# --- a real server, in a fresh interpreter -------------------------------------


@pytest.fixture
def warm(serve_session: ServeSession) -> WarmSession:
    return serve_session("warm")


@pytest.fixture
def send(hsql_subprocess: HsqlSubprocess, warm: WarmSession) -> HsqlSubprocess:
    """One invocation, sent to the session by the real console script."""

    def _send(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return hsql_subprocess(["--session", warm.name, *argv], env=warm.env, **kwargs)

    return _send


@needs_unix_sockets
def test_a_session_answers(send: HsqlSubprocess, warm: WarmSession) -> None:
    proc = send(["-c", "select 1 as a"])
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b" a\n---\n 1\n(1 row)\n"
    assert proc.stderr == b""


@needs_unix_sockets
@pytest.mark.session_divergent
def test_a_temp_table_survives_between_invocations(
    send: HsqlSubprocess, hsql_subprocess: HsqlSubprocess
) -> None:
    """The single most useful thing about the feature, and the one nobody reads
    the docs to discover. Cold, the second invocation has no such table."""
    assert (
        send(
            ["--format", "none", "-c", "create temp table t as select 7 as v"]
        ).returncode
        == 0
    )
    warm_result = send(["-tAc", "select v from t"])
    assert warm_result.returncode == ExitCode.OK
    assert warm_result.stdout == b"7\n"
    cold = ["-a", "duckdb", "--no-init", ":memory:"]
    assert (
        hsql_subprocess(
            [*cold, "-c", "create temp table t as select 7 as v"]
        ).returncode
        == 0
    )
    cold_result = hsql_subprocess([*cold, "-tAc", "select v from t"])
    assert cold_result.returncode == ExitCode.QUERY
    assert b"does not exist" in cold_result.stderr


@needs_unix_sockets
@pytest.mark.session_divergent
def test_a_setting_survives_between_invocations(send: HsqlSubprocess) -> None:
    assert send(["--format", "none", "-c", "set TimeZone='Asia/Tokyo'"]).returncode == 0
    proc = send(["-tAc", "select current_setting('TimeZone')"])
    assert proc.stdout == b"Asia/Tokyo\n"


@needs_unix_sockets
@pytest.mark.session_divergent
def test_a_reset_clears_the_sessions_state(send: HsqlSubprocess) -> None:
    send(["--format", "none", "-c", "create temp table t as select 7 as v"])
    proc = send(["--session-reset"])
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b""
    assert b"reconnected" in proc.stderr
    assert send(["-c", "select v from t"]).returncode == ExitCode.QUERY


@needs_unix_sockets
def test_a_relative_output_path_is_the_clients(
    send: HsqlSubprocess, tmp_path: Path
) -> None:
    """The server writes `-o`, resolved where the caller is, not where it is."""
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    proc = send(["--csv", "-o", "out.csv", "-c", "select 1 as a"], cwd=client_dir)
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b""
    assert (client_dir / "out.csv").read_bytes() == b"a\n1\n"


@needs_unix_sockets
def test_a_relative_script_path_is_the_clients(
    send: HsqlSubprocess, tmp_path: Path
) -> None:
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    (client_dir / "q.sql").write_text("select 'from a file' as a")
    proc = send(["-tAf", "q.sql"], cwd=client_dir)
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b"from a file\n"


@needs_unix_sockets
def test_a_config_file_where_the_client_is_applies_its_per_request_keys(
    send: HsqlSubprocess, tmp_path: Path
) -> None:
    """Config discovery is cwd-dependent, and the cwd is the client's."""
    client_dir = tmp_path / "project"
    client_dir.mkdir()
    (client_dir / ".harlequin.toml").write_text(
        'default_profile = "here"\n[profiles.here]\nformat = "csv"\n'
    )
    proc = send(["-c", "select 1 as a"], cwd=client_dir)
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b"a\n1\n"


@needs_unix_sockets
def test_a_typed_profile_resolves_where_the_client_is(
    send: HsqlSubprocess, tmp_path: Path
) -> None:
    """The session must resolve a `-P` against the *client's* directory, or a
    caller in a project would silently get a profile they did not write."""
    client_dir = tmp_path / "project"
    client_dir.mkdir()
    (client_dir / ".harlequin.toml").write_text(
        '[profiles.local]\nformat = "csv"\nlimit = 3\n'
    )
    proc = send(["-P", "local", "-c", "select 1 as a"], cwd=client_dir)
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b"a\n1\n"


@needs_unix_sockets
def test_the_config_path_the_caller_exported_travels_with_the_request(
    warm: WarmSession, hsql_subprocess: HsqlSubprocess, tmp_path: Path
) -> None:
    """`HARLEQUIN_CONFIG_PATH` is `--config-path` spelled as an environment
    variable, so a session that honored the flag and ignored the variable
    would read a config file neither of them named."""
    named = tmp_path / "elsewhere.toml"
    named.write_text('default_profile = "here"\n[profiles.here]\nformat = "csv"\n')
    proc = hsql_subprocess(
        ["--session", warm.name, "-c", "select 1 as a"],
        env={**warm.env, "HARLEQUIN_CONFIG_PATH": str(named)},
    )
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b"a\n1\n"


@needs_unix_sockets
def test_the_servers_own_config_path_is_not_the_requests(
    serve_session: ServeSession, hsql_subprocess: HsqlSubprocess, tmp_path: Path
) -> None:
    """The forwarded environment is the caller's: a variable the caller did not
    set arrives absent, rather than as whatever the operator exported."""
    servers = tmp_path / "server.toml"
    servers.write_text('default_profile = "there"\n[profiles.there]\nformat = "csv"\n')
    session = serve_session(
        "envd",
        "-a",
        "duckdb",
        "--no-init",
        ":memory:",
        env={"HARLEQUIN_CONFIG_PATH": str(servers)},
    )
    proc = hsql_subprocess(
        ["--session", "envd", "-c", "select 1 as a"], env=session.env
    )
    assert proc.returncode == ExitCode.OK
    # the table layout, and not the csv the server's own config file asks for
    assert proc.stdout == b" a\n---\n 1\n(1 row)\n"


@needs_unix_sockets
def test_piped_sql_reaches_the_session(send: HsqlSubprocess) -> None:
    proc = send(["-f", "-", "--csv"], stdin=b"select 3 as three")
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b"three\n3\n"


def test_a_session_with_no_connection_says_so(in_process_server: Server) -> None:
    """`state` is what tells a session that is merely busy from one a caller
    has to reset before it will answer anything."""
    assert in_process_server.status()["state"] == "idle"
    in_process_server.abandon()
    assert in_process_server.status()["state"] == "unavailable"


def test_a_status_carries_no_secret(duckdb_adapter: Any) -> None:
    """A session's command line is long-lived and visible in `ps`, and this is
    the document that would otherwise put its credentials in a caller's
    stdout."""
    adapter = duckdb_adapter([":memory:"], no_init=True)
    session = Server(
        "dsn",
        adapter="duckdb",
        connection=adapter.connect(),
        reconnect=adapter.connect,
        identity={
            "conn_str": ("postgres://ted:hunter2@warehouse:5432/analytics",),
            "password": "hunter2",
        },
    )
    status = session.status()
    assert status["connection"] == "postgres://ted:********@warehouse:5432/analytics"
    assert status["connection_options"]["password"] == "********"
    assert "hunter2" not in json.dumps(status)


@needs_unix_sockets
def test_a_session_says_what_it_is(send: HsqlSubprocess, warm: WarmSession) -> None:
    """The whole of `--session-status`: JSON on stdout, and the identity the
    session recorded when it connected."""
    send(["--format", "none", "-c", "select 1"])
    proc = send(["--session-status"])
    assert proc.returncode == ExitCode.OK
    assert proc.stderr == b""
    status = json.loads(proc.stdout)
    assert status["session"] == "warm"
    assert status["adapter"] == "duckdb"
    assert status["connection"] == ":memory:"
    assert status["connection_options"]["no_init"] is True
    assert status["version"] == protocol.VERSION
    assert status["pid"] == warm.process.pid
    assert status["requests"] == 1
    assert status["state"] == "idle"
    assert status["queued"] == 0
    assert status["ssh"] is None
    assert status["uptime_s"] >= 0


def test_a_busy_session_does_not_ask_its_driver_for_the_mode(
    duckdb_adapter: Any,
) -> None:
    """The one field a status has to ask the connection for, so it takes a
    turn rather than reading beside one -- and reports null instead of
    waiting for it. DuckDB has no transaction mode, so the label comes from a
    stand-in; what is pinned is that a held turnstile suppresses the read."""
    adapter = duckdb_adapter([":memory:"], no_init=True)
    connection = adapter.connect()
    reads: list[int] = []

    class Transacting:
        def __getattr__(self, name: str) -> Any:
            return getattr(connection, name)

        @property
        def transaction_mode(self) -> HarlequinTransactionMode:
            reads.append(1)
            return HarlequinTransactionMode(label="Manual")

    session = Server(
        "tx",
        adapter="duckdb",
        connection=cast(Any, Transacting()),
        reconnect=adapter.connect,
    )
    assert session.status()["transaction_mode"] == "Manual"
    assert reads == [1]
    assert session._turnstile.enter(0)
    try:
        busy = session.status()
    finally:
        session._turnstile.leave()
    assert busy["state"] == "busy"
    assert busy["transaction_mode"] is None
    # not merely null: the driver was never asked
    assert reads == [1]


@needs_unix_sockets
def test_a_status_is_answered_while_a_request_holds_the_connection(
    blocked: Blocked, warm: WarmSession, hsql_subprocess: HsqlSubprocess
) -> None:
    """A status takes no turn at the connection. The `blocked` fixture holds
    one deterministically -- it is inside `_run`, reading its script from a
    FIFO -- so a status that started queueing would time out here rather than
    hang the run."""
    proc = hsql_subprocess(
        ["--session", warm.name, "--session-status"], env=warm.env, timeout=30
    )
    assert proc.returncode == ExitCode.OK
    assert json.loads(proc.stdout)["state"] == "busy"
    blocked.release()
    assert blocked.process.wait(30) == ExitCode.OK


@needs_unix_sockets
def test_a_status_is_answered_while_the_database_is_working(
    warm: WarmSession, hsql_subprocess: HsqlSubprocess, tmp_path: Path
) -> None:
    """The stronger half of the claim: the status arrives while duckdb is
    executing, not merely while a request holds the turnstile. A driver call
    holding the GIL or a process-wide lock would serialize the status, and a
    FIFO read releases the GIL.

    `--timeout` bounds the query, so the session is free again for teardown.
    It only has to outlive the poll below, which returns on the first busy
    status."""
    query = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys\n"
            f"sys.argv = ['hsql', '--session', {warm.name!r}, '--timeout', "
            f"'5', '-c', {UNFINISHABLE!r}]\n"
            "from harlequin.hsql import main\n"
            "main()\n",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        env={**os.environ, **warm.env, "HOME": str(tmp_path)},
    )
    try:
        answered = _until_status(
            lambda: json.loads(
                hsql_subprocess(
                    ["--session", warm.name, "--session-status"],
                    env=warm.env,
                    timeout=30,
                ).stdout
            ),
            lambda status: status["state"] == "busy",
        )
        assert answered["state"] == "busy"
        assert answered["queued"] == 0
    finally:
        query.kill()
        query.communicate(timeout=30)


def _until_status(
    ask: Callable[[], dict[str, Any]],
    holds: Callable[[dict[str, Any]], bool],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Ask until the answer holds, so the test observes rather than sleeps."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = ask()
        if holds(status):
            return status
    raise AssertionError("the session never reported what the test waited for")


@needs_unix_sockets
def test_a_status_takes_nothing_beside_it(send: HsqlSubprocess) -> None:
    """No parser sees a status ask, so a flag typed beside one is refused by
    name rather than silently dropped."""
    proc = send(["--session-status", "--csv"])
    assert proc.returncode == ExitCode.USAGE
    assert proc.stdout == b""
    assert b"takes no other options" in proc.stderr


@needs_unix_sockets
def test_a_mistyped_status_does_not_wait_for_the_query_it_asks_about(
    blocked: Blocked, warm: WarmSession, hsql_subprocess: HsqlSubprocess
) -> None:
    """`--session-status=1` is a typo click refuses either way, but one the
    scan missed would reach the session as a request and wait for a running
    query to say so."""
    proc = hsql_subprocess(
        ["--session", warm.name, "--session-status=1"], env=warm.env, timeout=30
    )
    assert proc.returncode == ExitCode.USAGE
    assert proc.stdout == b""
    assert b"--session-status is a flag and takes no value" in proc.stderr
    blocked.release()
    assert blocked.process.wait(30) == ExitCode.OK


def test_a_bug_in_the_status_path_is_exit_70_and_not_someone_elses_stderr(
    in_process_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An escaping traceback would reach `threading.excepthook`, which writes
    to the stderr the in-flight *request* has swapped in -- so a bug here
    would print a server traceback into an unrelated caller's diagnostics."""
    monkeypatch.setattr(Server, "status", lambda self: 1 / 0, raising=True)
    left, right = socket.socketpair()
    with left, right:
        in_process_server._send_status(left, ["--session-status"])
        left.shutdown(socket.SHUT_WR)
        frames = []
        while (frame := protocol.recv_frame(right)) is not None:
            frames.append(frame)
    assert frames[-1] == (protocol.EXIT, bytes([ExitCode.CRASH]))
    said = b"".join(payload for kind, payload in frames if kind == protocol.STDERR)
    assert b"hsql hit a bug in itself" in said
    assert b"Traceback" not in said


@needs_unix_sockets
def test_a_status_is_not_a_request_the_session_counts(
    send: HsqlSubprocess, warm: WarmSession
) -> None:
    send(["--session-status"])
    assert warm.stop() == ExitCode.OK
    assert "--session-status: exit 0" in warm.stderr()
    assert "session 'warm' stopped after 0 requests" in warm.stderr()


@needs_unix_sockets
def test_a_request_that_asserts_the_sessions_connection_is_served(
    send: HsqlSubprocess,
) -> None:
    """End to end: the identity the server recorded is what a request's typed
    connection options are compared against."""
    assert send(["-a", "duckdb", "-tAc", "select 1"]).returncode == ExitCode.OK
    refused = send(["-a", "sqlite", "-c", "select 1"])
    assert refused.returncode == ExitCode.USAGE
    assert b"was started with 'duckdb'" in refused.stderr


@needs_unix_sockets
def test_the_servers_own_streams_carry_only_its_log(
    warm: WarmSession, send: HsqlSubprocess
) -> None:
    """Result sets go to the client that asked; the operator sees a line per request."""
    send(["-c", "select 'to the client' as a"])
    send(["-c", "select * from nowhere"])
    assert warm.stop() == ExitCode.OK
    log = warm.stderr()
    assert "to the client" not in log
    assert "session 'warm' is ready (duckdb)" in log
    assert "request 1: exit 0" in log
    assert "request 2: exit 1" in log
    assert "session 'warm' stopped after 2 requests" in log
    assert warm.process.stdout is not None


@needs_unix_sockets
def test_a_stopped_session_takes_its_socket_with_it(warm: WarmSession) -> None:
    assert warm.socket_path.exists()
    assert warm.stop() == ExitCode.OK
    assert not warm.socket_path.exists()


@needs_unix_sockets
def test_a_second_server_under_the_same_name_is_refused(
    warm: WarmSession, serve_session: ServeSession
) -> None:
    second = serve_session("warm", wait=False)
    assert second.process.wait(30) == ExitCode.USAGE
    assert "already running" in second.stderr()
    # and the first is untouched: its socket is still the one that answers
    assert warm.socket_path.exists()


@needs_unix_sockets
def test_a_stale_socket_is_replaced(
    short_runtime_dir: Path, serve_session: ServeSession
) -> None:
    """A server that died leaves its file; the next one under that name is
    what unlinks it, since a client never does."""
    from harlequin.hsql.session import socket_path

    (short_runtime_dir / "hsql").mkdir(mode=0o700)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(socket_path("warm", {"XDG_RUNTIME_DIR": str(short_runtime_dir)}))
    stale.close()
    session = serve_session("warm")
    assert session.socket_path.exists()
    assert session.stop() == ExitCode.OK


@needs_unix_sockets
def test_a_runtime_dir_someone_else_can_reach_is_refused(
    short_runtime_dir: Path, serve_session: ServeSession
) -> None:
    shared = short_runtime_dir / "hsql"
    shared.mkdir()
    os.chmod(shared, 0o777)
    session = serve_session("warm", wait=False)
    assert session.process.wait(30) == ExitCode.USAGE
    assert "cannot listen" in session.stderr()


@needs_unix_sockets
def test_serve_on_a_connection_that_fails_exits_3(serve_session: ServeSession) -> None:
    session = serve_session(
        "warm", "-a", "duckdb", "--no-init", "/nonexistent/dir/x.db", wait=False
    )
    assert session.process.wait(30) == ExitCode.CONNECTION


class Blocked:
    """A request the test holds open: the server is reading a FIFO for its SQL.

    Deterministic where a slow query is not: opening the FIFO for writing
    blocks until the server has opened it to read, which is after the request
    took its turn, so everything sent after that is queued behind it.
    """

    def __init__(self, fifo: Path, warm: WarmSession, tmp_path: Path) -> None:
        self.fifo = fifo
        os.mkfifo(fifo)
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys\n"
                f"sys.argv = ['hsql', '--session', {warm.name!r}, "
                f"'--format', 'none', '-f', {str(fifo)!r}]\n"
                "from harlequin.hsql import main\n"
                "main()\n",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmp_path,
            env={**os.environ, **warm.env, "HOME": str(tmp_path)},
        )
        self.writer = open(fifo, "w")

    def release(self, sql: str = "select 1") -> None:
        self.writer.write(sql)
        self.writer.close()


@pytest.fixture
def blocked(
    warm: WarmSession, short_runtime_dir: Path, tmp_path: Path
) -> Iterator[Blocked]:
    held = Blocked(short_runtime_dir / "script.fifo", warm, tmp_path)
    yield held
    if held.process.poll() is None:
        held.process.kill()
    held.process.wait(5)
    if held.process.stdout is not None:
        held.process.stdout.close()
    if held.process.stderr is not None:
        held.process.stderr.close()


@needs_unix_sockets
@pytest.mark.session_divergent
def test_a_second_client_waits_its_turn(blocked: Blocked, send: HsqlSubprocess) -> None:
    """One connection, one request at a time: the second client's query sees
    what the first one, still running when it was sent, went on to create."""
    second = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "sys.argv = ['hsql', '--session', 'warm', '-tAc', 'select v from q']\n"
            "from harlequin.hsql import main\n"
            "main()\n",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, **blocked_env(blocked)},
    )
    blocked.release("create temp table q as select 42 as v")
    out, err = second.communicate(timeout=30)
    assert second.returncode == ExitCode.OK, err
    assert out == b"42\n"
    assert blocked.process.wait(30) == ExitCode.OK


def blocked_env(blocked: Blocked) -> dict[str, str]:
    return {"XDG_RUNTIME_DIR": str(blocked.fifo.parent)}


@needs_unix_sockets
def test_a_client_that_dies_mid_query_does_not_take_the_session_down(
    blocked: Blocked, send: HsqlSubprocess, warm: WarmSession
) -> None:
    blocked.process.kill()
    blocked.process.wait(5)
    blocked.release("select 1")
    proc = send(["-tAc", "select 'still up'"])
    assert proc.returncode == ExitCode.OK
    assert proc.stdout == b"still up\n"
    assert warm.stop() == ExitCode.OK
    assert "a client went away" in warm.stderr()


@needs_unix_sockets
def test_a_timeout_stops_the_request_and_leaves_the_session_up(
    send: HsqlSubprocess,
) -> None:
    """The whole chain a served `--timeout` crosses: the deadline cancels, the
    request attributes it and exits 4, and the session is usable straight
    after -- DuckDB's cancel lands inside the grace period, so the connection
    is never abandoned. (The grace running out is `Deadline`'s own test: no
    adapter that cancels can reach it.)"""
    proc = send(["--timeout", "0.1", "-c", UNFINISHABLE])
    assert proc.returncode == ExitCode.TIMEOUT
    assert proc.stdout == b""
    assert b"timed out after 0.1s" in proc.stderr
    assert send(["-tAc", "select 'still up'"]).stdout == b"still up\n"


@needs_unix_sockets
def test_a_queue_timeout_is_not_a_query_timeout(
    serve_session: ServeSession,
    hsql_subprocess: HsqlSubprocess,
    short_runtime_dir: Path,
    tmp_path: Path,
) -> None:
    """A client that never reached the database exits 4 saying so -- a
    different fact from a query that ran too long, and one the caller acts on
    differently."""
    session = serve_session(
        "queued", "--queue-timeout", "0.2", "-a", "duckdb", "--no-init", ":memory:"
    )
    held = Blocked(short_runtime_dir / "q.fifo", session, tmp_path)
    try:
        proc = hsql_subprocess(
            ["--session", "queued", "-c", "select 1"], env=session.env
        )
        assert proc.returncode == ExitCode.TIMEOUT
        assert proc.stdout == b""
        assert b"never reached the database" in proc.stderr
        assert b"--queue-timeout" in proc.stderr
    finally:
        held.release()
        held.process.wait(30)
    assert (
        hsql_subprocess(
            ["--session", "queued", "-tAc", "select 2"], env=session.env
        ).stdout
        == b"2\n"
    )


# --- the wire, from a client that is not ours -------------------------------------


def raw_request(
    warm: WarmSession, **fields: Any
) -> tuple[list[tuple[int, bytes]], int]:
    """Send one request over the protocol by hand, and return what came back."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with connection:
        connection.connect(str(warm.socket_path))
        hello = protocol.recv_frame(connection)
        assert hello == (protocol.HELLO, protocol.VERSION.encode())
        request = {
            "argv": [],
            "cwd": os.getcwd(),
            "environ": {},
            "stdin": None,
            **fields,
        }
        protocol.send_frame(
            connection, protocol.REQUEST, protocol.pack_request(**request)
        )
        frames: list[tuple[int, bytes]] = []
        while True:
            frame = protocol.recv_frame(connection)
            assert frame is not None
            if frame[0] == protocol.EXIT:
                return frames, int.from_bytes(frame[1], "big")
            frames.append(frame)


@needs_unix_sockets
def test_the_server_refuses_a_dash_f_dash_that_carried_no_stdin(
    warm: WarmSession,
) -> None:
    """The check that turns every miss of the client's argv scan into an exit 2."""
    frames, code = raw_request(warm, argv=["-f", "-"], stdin=None)
    assert code == ExitCode.USAGE
    assert b"carried none" in b"".join(
        data for kind, data in frames if kind == protocol.STDERR
    )


@needs_unix_sockets
def test_the_server_refuses_a_working_directory_it_cannot_enter(
    warm: WarmSession,
) -> None:
    frames, code = raw_request(
        warm, argv=["-c", "select 1"], cwd="/nonexistent/anywhere"
    )
    assert code == ExitCode.USAGE
    assert b"cannot be entered" in b"".join(
        data for kind, data in frames if kind == protocol.STDERR
    )


@needs_unix_sockets
def test_the_servers_stdout_is_chunked(warm: WarmSession) -> None:
    """Many `STDOUT` frames then one `EXIT`, so that streaming has somewhere to land."""
    wide = "select repeat('x', 1000) as a from range(200)"
    frames, code = raw_request(warm, argv=["-tA", "--display-rows", "-1", "-c", wide])
    assert code == ExitCode.OK
    stdout = [data for kind, data in frames if kind == protocol.STDOUT]
    assert len(stdout) > 1
    assert all(len(chunk) <= protocol.CHUNK_SIZE for chunk in stdout)
    assert b"".join(stdout) == (b"x" * 1000 + b"\n") * 200


@needs_unix_sockets
def test_a_client_that_sends_no_request_is_dropped(
    warm: WarmSession, send: HsqlSubprocess
) -> None:
    """A version it refused to be served by, or one that died: the server
    reads the hello, hears nothing back, and goes on serving."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with connection:
        connection.connect(str(warm.socket_path))
        assert protocol.recv_frame(connection) is not None
    assert send(["-tAc", "select 1"]).stdout == b"1\n"


@needs_unix_sockets
def test_color_auto_reads_the_callers_terminal(warm: WarmSession) -> None:
    """On the server every stream is a buffer, so the request has to say."""
    argv = ["--color", "auto", "-c", "select 1 as a"]
    plain, _ = raw_request(warm, argv=argv, stdout_isatty=False)
    styled, _ = raw_request(warm, argv=argv, stdout_isatty=True)
    assert b"\x1b[" not in b"".join(data for _, data in plain)
    assert b"\x1b[" in b"".join(data for _, data in styled)
    honored, _ = raw_request(
        warm, argv=argv, stdout_isatty=True, environ={"NO_COLOR": "1"}
    )
    assert b"\x1b[" not in b"".join(data for _, data in honored)


@needs_unix_sockets
def test_the_peer_uid_is_ours_on_a_socketpair() -> None:
    left, right = socket.socketpair(socket.AF_UNIX)
    with left, right:
        uid = server.peer_uid(right)
    assert uid is None or uid == os.getuid()
