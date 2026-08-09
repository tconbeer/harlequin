"""hsql's contract: what lands on stdout, what lands on stderr, and the code.

The output format and the exit codes are the part of hsql that is an API, so
most of what is asserted here is a promise rather than an implementation: that
stdout carries data and only data, that a truncated result says so, that the
same query renders the same bytes wherever it is run.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

import pytest
from click.testing import CliRunner, Result

from harlequin.exception import (
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinCopyError,
    HarlequinQueryError,
)
from harlequin.hsql.cli import build_cli
from harlequin.hsql.diagnostics import ExitCode, exit_code_for

Hsql = Callable[..., Result]

TEN_ROWS = (
    "with recursive t(n) as ("
    "select 1 union all select n + 1 from t where n < 10"
    ") select n from t"
)
"""Ten rows, in a spelling both bundled adapters accept."""

LAYOUTS = ["table", "markdown", "md", "vertical"]
TEXT_FILES = ["csv", "tsv", "json", "jsonl", "ndjson"]
BINARY_FILES = ["parquet", "orc", "arrow"]
FILE_FORMATS = TEXT_FILES + BINARY_FILES

# the console script's entry point, for the assertions that need a real process
HSQL_MAIN = (
    "import sys; from harlequin.hsql import main; "
    "sys.argv = ['hsql', *sys.argv[1:]]; main()"
)


@pytest.fixture(autouse=True)
def no_discovered_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the machine running the tests out of them.

    Config discovery walks the home directory and the cwd, so without this a
    developer's own `.harlequin.toml` decides what these assert.
    """
    for search in ("_search_home", "_search_config", "_search_cwd"):
        monkeypatch.setattr(f"harlequin.config.{search}", list)


@pytest.fixture
def hsql() -> Hsql:
    """Invoke hsql the way the console script does, in process."""
    runner = CliRunner()

    def _run(*args: str, **kwargs: Any) -> Result:
        argv = [str(arg) for arg in args]
        return runner.invoke(build_cli(argv), argv, catch_exceptions=False, **kwargs)

    return _run


@pytest.fixture
def duck() -> list[str]:
    """The arguments that get a hermetic in-memory DuckDB."""
    return ["-a", "duckdb", "--no-init", ":memory:"]


@pytest.fixture(params=["duckdb", "sqlite"])
def both_adapters(request: pytest.FixtureRequest) -> list[str]:
    return ["-a", request.param, "--no-init", ":memory:"]


def run_hsql(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    """Run hsql in a real process, for the assertions that are about bytes."""
    return subprocess.run(
        [sys.executable, "-c", HSQL_MAIN, *args], capture_output=True, **kwargs
    )


# --- help, and what it costs -------------------------------------------------


def test_help_is_adapter_agnostic(hsql: Hsql) -> None:
    res = hsql("--help")
    assert res.exit_code == ExitCode.OK
    assert "Formats:" in res.output
    assert "Exit codes:" in res.output
    assert "Installed adapters:" in res.output
    assert "hsql --help -a <adapter>" in res.output
    # no adapter's connection options, and none of the IDE's options either
    assert "--read-only" not in res.output
    assert "--theme" not in res.output
    assert "--show-files" not in res.output


def test_help_for_one_adapter(hsql: Hsql) -> None:
    res = hsql("--help", "-a", "duckdb")
    assert res.exit_code == ExitCode.OK
    assert "--read-only" in res.output
    assert "Showing duckdb's connection options." in res.output


def test_plain_help_imports_no_adapter() -> None:
    """The point of the two-phase parse, and the thing that regresses quietly."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from harlequin.hsql.cli import build_cli; "
            "build_cli(['--help']); "
            "print(any(m.startswith('harlequin_') for m in sys.modules))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "False"


def test_bare_invocation_prints_help_and_leaves_stdout_empty(hsql: Hsql) -> None:
    """An invocation with nothing to run is a usage error, not a result.

    Putting the help on stdout would mean `hsql | ...` could carry help text
    where data belongs, which is the one thing stdout is not allowed to do.
    """
    res = hsql()
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "Usage:" in res.stderr


def test_no_sql_to_run(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck)
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "no SQL to run" in res.stderr


# --- the shapes of output ----------------------------------------------------


def test_select_1(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "-c", "select 1 as a")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == " a\n---\n 1\n(1 row)\n"
    assert re.fullmatch(r"1 row in \d+\.\d\ds\n", res.stderr)


def test_the_tac_idiom(hsql: Hsql, duck: list[str]) -> None:
    """`-tAc` capturing a scalar is the first thing a script will try."""
    res = hsql(*duck, "-tAc", "select 42")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "42\n"


@pytest.mark.parametrize(
    "args,expected",
    [
        ([], " a\n---\n 1\n(1 row)\n"),
        (["-t"], " 1\n"),
        (["-A"], "a\n1\n(1 row)\n"),
        (["--no-header"], " 1\n(1 row)\n"),
        (["-tA"], "1\n"),
    ],
)
def test_psql_flag_algebra(
    hsql: Hsql, duck: list[str], args: list[str], expected: str
) -> None:
    """`-t` and `-A` are independent switches, so `-tA` is not a special case."""
    res = hsql(*duck, *args, "-c", "select 1 as a")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == expected


@pytest.mark.parametrize("format_name", LAYOUTS + FILE_FORMATS + ["none"])
def test_every_format_runs(hsql: Hsql, duck: list[str], format_name: str) -> None:
    res = hsql(*duck, "-F", format_name, "-c", "select 1 as a, null as b")
    assert res.exit_code == ExitCode.OK
    assert bool(res.stdout_bytes) is (format_name != "none")


@pytest.mark.parametrize(
    "flag,format_name",
    [
        ("--csv", "csv"),
        ("--json", "json"),
        ("--jsonl", "jsonl"),
        ("--markdown", "markdown"),
        ("--vertical", "vertical"),
    ],
)
def test_format_shorthands(
    hsql: Hsql, duck: list[str], flag: str, format_name: str
) -> None:
    sql = "select 1 as a"
    assert (
        hsql(*duck, flag, "-c", sql).stdout_bytes
        == hsql(*duck, "-F", format_name, "-c", sql).stdout_bytes
    )


@pytest.mark.parametrize(
    "args", [["--csv", "--json"], ["--csv", "-F", "json"], ["-F", "json", "--csv"]]
)
def test_two_formats_is_a_usage_error(
    hsql: Hsql, duck: list[str], args: list[str]
) -> None:
    res = hsql(*duck, *args, "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert res.stderr.startswith("hsql: error: ")


def test_null_string(hsql: Hsql, duck: list[str]) -> None:
    assert " NULL\n" in hsql(*duck, "-c", "select null as a").stdout
    assert " ∅\n" in hsql(*duck, "--null-string", "∅", "-c", "select null as a").stdout
    # csv renders a null as nothing at all unless told otherwise
    assert hsql(*duck, "--csv", "-c", "select null as a").stdout == "a\n\n"
    assert (
        hsql(*duck, "--csv", "--null-string", "∅", "-c", "select null as a").stdout
        == "a\n∅\n"
    )


def test_zero_rows_is_not_an_error(hsql: Hsql, duck: list[str]) -> None:
    """A0 vs A3: "matched nothing" has to be distinguishable from "failed"."""
    res = hsql(*duck, "--csv", "-c", "select 1 as a where false")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "a\n"
    assert "0 rows" in res.stderr


# --- stdout is data ----------------------------------------------------------


@pytest.mark.parametrize("format_name", LAYOUTS + FILE_FORMATS)
def test_stats_does_not_touch_stdout(
    hsql: Hsql, duck: list[str], format_name: str
) -> None:
    sql = "select 1 as a, 'x' as b"
    plain = hsql(*duck, "-F", format_name, "-c", sql)
    with_stats = hsql(*duck, "-F", format_name, "--stats", "-c", sql)
    assert plain.stdout_bytes == with_stats.stdout_bytes
    assert with_stats.stderr != plain.stderr


def test_stats_payload(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "-F", "none", "--stats", "-l", "3", "-c", TEN_ROWS)
    payload = json.loads(res.stderr.splitlines()[-1])
    assert payload == {
        "status": "ok",
        "statements": 1,
        "rows": 3,
        "truncated": True,
        "limit": 3,
        "elapsed_ms": payload["elapsed_ms"],
        "columns": [{"name": "n", "type": payload["columns"][0]["type"]}],
    }


def test_stats_reports_a_failure(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "--stats", "-c", "select from nowhere")
    payload = json.loads(res.stderr.splitlines()[-1])
    assert payload["status"] == "error"
    assert payload["rows"] == 0
    assert payload["error"]


def test_a_query_error_leaves_stdout_empty(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "-c", "select * from no_such_table")
    assert res.exit_code == ExitCode.QUERY
    assert res.stdout == ""
    assert res.stderr.startswith("hsql: error: ")
    # plain: no panel, no box drawing, no ANSI
    assert "─" not in res.stderr
    assert "\x1b[" not in res.stderr


# --- truncation --------------------------------------------------------------


@pytest.mark.parametrize(
    "limit,rows,truncated", [(20, 10, False), (10, 10, False), (3, 3, True)]
)
def test_truncation(
    hsql: Hsql, both_adapters: list[str], limit: int, rows: int, truncated: bool
) -> None:
    """Exactly at the limit is the ambiguous case, and the reason for limit+1."""
    res = hsql(*both_adapters, "-l", str(limit), "-c", TEN_ROWS)
    assert res.exit_code == ExitCode.OK
    body = res.stdout.splitlines()[2:-1]  # between the rule and the footer
    assert len(body) == rows
    assert ("truncated" in res.stderr) is truncated
    assert (f"({rows} of {rows}+ rows)" in res.stdout) is truncated


def test_no_limit_counts_exactly(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "-l", "0", "--stats", "-F", "none", "-c", TEN_ROWS)
    payload = json.loads(res.stderr.splitlines()[-1])
    assert payload["rows"] == 10
    assert payload["truncated"] is False
    assert payload["limit"] is None


def test_the_truncation_notice_survives_tuples_only(
    hsql: Hsql, duck: list[str]
) -> None:
    """`-t` suppresses stdout chrome. It does not suppress a warning."""
    res = hsql(*duck, "-t", "-l", "3", "-c", TEN_ROWS)
    assert "of 3+" not in res.stdout
    assert "results truncated at 3 rows (--limit)" in res.stderr


# --- several statements ------------------------------------------------------


def test_result_all_by_default(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "-tA", "-c", "select 1; select 2")
    assert res.stdout == "1\n2\n"


@pytest.mark.parametrize("spec,expected", [("last", "2\n"), ("1", "1\n"), ("2", "2\n")])
def test_result_selection(
    hsql: Hsql, duck: list[str], spec: str, expected: str
) -> None:
    res = hsql(*duck, "-tA", "--result", spec, "-c", "select 1; select 2")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == expected


@pytest.mark.parametrize("spec", ["3", "0", "penultimate"])
def test_bad_result_selection(hsql: Hsql, duck: list[str], spec: str) -> None:
    res = hsql(*duck, "--result", spec, "-c", "select 1; select 2")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""


def test_two_result_sets_will_not_fit_in_a_csv(hsql: Hsql, duck: list[str]) -> None:
    """Two headers in one file is silent corruption; an error costs one retry."""
    res = hsql(*duck, "--csv", "-c", "select 1; select 2")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "2 result sets, but csv holds one" in res.stderr
    assert "--result last" in res.stderr


def test_two_result_sets_do_fit_in_jsonl(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "--jsonl", "-c", "select 1 as a; select 2 as a")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == '{"a":1}\n{"a":2}\n'


def test_on_error_stop(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "-tA", "-c", "select 1; select bad; select 3")
    assert res.exit_code == ExitCode.QUERY
    assert res.stdout == "1\n"


def test_on_error_continue(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(
        *duck, "-tA", "--on-error", "continue", "-c", "select 1; select bad; select 3"
    )
    assert res.exit_code == ExitCode.QUERY, "a run with a failure in it is not ok"
    assert res.stdout == "1\n3\n"


def test_ddl_produces_no_result_set(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "-c", "create table t (a int)", "-c", "insert into t values (1)")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""


# --- where the SQL comes from ------------------------------------------------


def test_file_source(hsql: Hsql, duck: list[str], tmp_path: Path) -> None:
    script = tmp_path / "q.sql"
    script.write_text("select 42 as answer;\n")
    res = hsql(*duck, "-tA", "-f", str(script))
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "42\n"


def test_stdin_source(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "-tA", "-f", "-", input="select 42\n")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "42\n"


def test_sources_run_in_the_order_they_were_given(
    hsql: Hsql, duck: list[str], tmp_path: Path
) -> None:
    script = tmp_path / "setup.sql"
    script.write_text("create table t (a int); insert into t values (7);")
    res = hsql(*duck, "-tA", "-f", str(script), "-c", "select a from t")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "7\n"


def test_a_missing_file_is_a_usage_error(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "-f", "no-such-file.sql")
    assert res.exit_code == ExitCode.USAGE
    assert "could not read" in res.stderr


# --- writing to a file -------------------------------------------------------


@pytest.mark.parametrize(
    "format_name,sql",
    [
        *(
            (name, "select 1 as a, null as b, 'ü' as c")
            for name in LAYOUTS + TEXT_FILES
        ),
        # A null's value bytes are whatever was in the buffer, and Arrow does
        # not zero them -- so two processes writing the same null column write
        # different padding. That is the data, not the write path, so the
        # columnar formats are checked without one.
        *((name, "select 1 as a, 'ü' as c") for name in BINARY_FILES),
    ],
)
def test_output_file_and_redirect_agree(
    hsql: Hsql, duck: list[str], tmp_path: Path, format_name: str, sql: str
) -> None:
    """The one thing a second write path could plausibly get wrong."""
    destination = tmp_path / f"out.{format_name}"
    written = hsql(*duck, "-F", format_name, "-o", str(destination), "-c", sql)
    piped = hsql(*duck, "-F", format_name, "-c", sql)
    assert written.exit_code == ExitCode.OK
    assert written.stdout_bytes == b""
    assert destination.read_bytes() == piped.stdout_bytes


def test_a_file_never_gets_color(hsql: Hsql, duck: list[str], tmp_path: Path) -> None:
    destination = tmp_path / "out.txt"
    hsql(*duck, "--color", "always", "-o", str(destination), "-c", "select 1 as a")
    assert b"\x1b[" not in destination.read_bytes()


def test_an_unwritable_destination_is_a_usage_error(
    hsql: Hsql, duck: list[str], tmp_path: Path
) -> None:
    res = hsql(*duck, "-o", str(tmp_path / "nope" / "out.csv"), "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stderr.startswith("hsql: error: ")


# --- color -------------------------------------------------------------------


def test_color_is_off_by_default(hsql: Hsql, duck: list[str]) -> None:
    """Principle 4: the bytes may not depend on what stdout happens to be."""
    assert "\x1b[" not in hsql(*duck, "-c", "select 1 as a").stdout


def test_color_always(hsql: Hsql, duck: list[str]) -> None:
    assert "\x1b[" in hsql(*duck, "--color", "always", "-c", "select null as a").stdout


# --- config, profiles and adapters -------------------------------------------


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / ".harlequin.toml"
    path.write_text(
        "default_profile = 'duck'\n"
        "\n[profiles.duck]\n"
        "adapter = 'duckdb'\n"
        "conn_str = [ ':memory:' ]\n"
        "no_init = true\n"
        "theme = 'fruity'\n"
        "locale = 'de_DE.UTF-8'\n"
        "limit = 3\n"
        "\n[profiles.lite]\n"
        "adapter = 'sqlite'\n"
        "conn_str = [ ':memory:' ]\n"
    )
    return path


def test_the_default_profile_applies(hsql: Hsql, config_file: Path) -> None:
    """And the IDE's own keys are dropped rather than handed to the adapter."""
    res = hsql("--config-path", str(config_file), "-tA", "-c", TEN_ROWS)
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "".join(f"{n}\n" for n in range(1, 4)), "profile limit of 3"


def test_a_cli_option_beats_the_profile(hsql: Hsql, config_file: Path) -> None:
    res = hsql("--config-path", str(config_file), "-tA", "-l", "2", "-c", TEN_ROWS)
    assert res.stdout == "1\n2\n"


def test_a_profile_names_its_adapter(hsql: Hsql, config_file: Path) -> None:
    res = hsql(
        "--config-path", str(config_file), "-P", "lite", "-tA", "-c", "select 42"
    )
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "42\n"


def test_help_for_the_profiles_adapter(hsql: Hsql, config_file: Path) -> None:
    res = hsql("--config-path", str(config_file), "-P", "lite", "--help")
    assert "Showing sqlite's connection options." in res.output


def test_an_unknown_profile_is_a_usage_error(hsql: Hsql, config_file: Path) -> None:
    res = hsql("--config-path", str(config_file), "-P", "nope", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "profile" in res.stderr


def test_an_unknown_adapter_is_a_usage_error(hsql: Hsql) -> None:
    res = hsql("-a", "nosuchadapter", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""


def test_a_connection_failure_exits_three(hsql: Hsql, tmp_path: Path) -> None:
    res = hsql(
        "-a",
        "duckdb",
        "--no-init",
        str(tmp_path / "nope" / "db.duckdb"),
        "-c",
        "select 1",
    )
    assert res.exit_code == ExitCode.CONNECTION
    assert res.stdout == ""


# --- the exit-code mapping ---------------------------------------------------


@pytest.mark.parametrize(
    "error,code",
    [
        (HarlequinQueryError("boom"), ExitCode.QUERY),
        (HarlequinConfigError("boom"), ExitCode.USAGE),
        (HarlequinConnectionError("boom"), ExitCode.CONNECTION),
        (HarlequinCopyError("boom"), ExitCode.QUERY),
        (KeyboardInterrupt(), ExitCode.INTERRUPT),
        (RuntimeError("boom"), ExitCode.QUERY),
    ],
)
def test_exit_code_for(error: BaseException, code: ExitCode) -> None:
    assert exit_code_for(error) == code


# --- the bytes are the contract ----------------------------------------------


@pytest.mark.parametrize("args", [[], ["--csv"], ["--json"], ["--markdown"]])
def test_output_is_lf_on_every_platform(args: Sequence[str]) -> None:
    proc = run_hsql(
        "-a", "duckdb", "--no-init", ":memory:", *args, "-c", "select 1 as a"
    )
    assert proc.returncode == ExitCode.OK
    assert b"\r\n" not in proc.stdout


def test_output_does_not_vary_with_the_locale() -> None:
    """The IDE groups digits for a human. hsql must not, on anyone's machine."""
    args = ("-a", "duckdb", "--no-init", ":memory:", "-c", "select 1234567 as n")
    plain = run_hsql(*args)
    localized = run_hsql(*args, env={**_environ(), "LC_ALL": "de_DE.UTF-8"})
    assert plain.returncode == ExitCode.OK
    assert b"1234567" in plain.stdout
    assert plain.stdout == localized.stdout


def test_output_does_not_vary_with_a_pipe(tmp_path: Path) -> None:
    args = ("-a", "duckdb", "--no-init", ":memory:", "-c", "select 1 as a, 'ü' as b")
    piped = run_hsql(*args).stdout
    destination = tmp_path / "out.txt"
    with destination.open("wb") as f:
        subprocess.run(
            [sys.executable, "-c", HSQL_MAIN, *args],
            stdout=f,
            stderr=subprocess.DEVNULL,
        )
    assert destination.read_bytes() == piped


def _environ() -> dict[str, str]:
    import os

    return dict(os.environ)
