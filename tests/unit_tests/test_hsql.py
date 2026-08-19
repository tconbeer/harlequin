"""hsql's contract: what lands on stdout, what lands on stderr, and the code.

The output format and the exit codes are the part of hsql that is an API, so
most of what is asserted here is a promise rather than an implementation: that
stdout carries data and only data, that a truncated result says so, that the
same query renders the same bytes wherever it is run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, cast

import click
import pytest
from click.testing import CliRunner, Result

from harlequin.exception import (
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinCopyError,
    HarlequinQueryError,
)
from harlequin.hsql.cli import PROGRAM, build_cli
from harlequin.hsql.diagnostics import IDE_THEMES, ExitCode, exit_code_for

Hsql = Callable[..., Result]

TEN_ROWS = (
    "with recursive t(n) as ("
    "select 1 union all select n + 1 from t where n < 10"
    ") select n from t"
)
"""Ten rows, in a spelling both bundled adapters accept."""

HUNDRED_ROWS = TEN_ROWS.replace("n < 10", "n < 100")
"""More rows than any layout prints by default, and fewer than --limit fetches."""

LAYOUTS = ["table", "markdown", "md", "vertical"]
TEXT_FILES = ["csv", "tsv", "json", "jsonl", "ndjson"]
BINARY_FILES = ["parquet", "orc", "arrow"]
FILE_FORMATS = TEXT_FILES + BINARY_FILES


@pytest.fixture
def hsql(no_discovered_config: None) -> Hsql:
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
    # the count is the footer's job. A run with nothing to warn about writes
    # nothing at all to stderr.
    assert res.stderr == ""


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
        (["--no-footer"], " a\n---\n 1\n"),
        (["--no-header", "--no-footer"], " 1\n"),
        (["-tA"], "1\n"),
    ],
)
def test_psql_flag_algebra(
    hsql: Hsql, duck: list[str], args: list[str], expected: str
) -> None:
    """`-t` and `-A` are independent switches, so `-tA` is not a special case.

    `--no-header --no-footer` is `-t` spelled out, and has to agree with it.
    """
    res = hsql(*duck, *args, "-c", "select 1 as a")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == expected


@pytest.mark.parametrize("format_name", LAYOUTS + FILE_FORMATS + ["none"])
def test_every_format_runs(hsql: Hsql, duck: list[str], format_name: str) -> None:
    res = hsql(*duck, "--format", format_name, "-c", "select 1 as a, null as b")
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
        == hsql(*duck, "--format", format_name, "-c", sql).stdout_bytes
    )


@pytest.mark.parametrize(
    "args",
    [["--csv", "--json"], ["--csv", "--format", "json"], ["--format", "json", "--csv"]],
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
    res = hsql(*duck, "--csv", "--stats", "-c", "select 1 as a where false")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "a\n"
    assert json.loads(res.stderr.splitlines()[-1])["rows"] == 0


# --- stdout is data ----------------------------------------------------------


@pytest.mark.parametrize("format_name", LAYOUTS + FILE_FORMATS)
def test_stats_does_not_touch_stdout(
    hsql: Hsql, duck: list[str], format_name: str
) -> None:
    sql = "select 1 as a, 'x' as b"
    plain = hsql(*duck, "--format", format_name, "-c", sql)
    with_stats = hsql(*duck, "--format", format_name, "--stats", "-c", sql)
    assert plain.stdout_bytes == with_stats.stdout_bytes
    assert with_stats.stderr != plain.stderr


def test_stats_payload(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "--format", "none", "--stats", "--limit", "3", "-c", TEN_ROWS)
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
    res = hsql(*both_adapters, "--limit", str(limit), "-c", TEN_ROWS)
    assert res.exit_code == ExitCode.OK
    body = res.stdout.splitlines()[2:-1]  # between the rule and the footer
    assert len(body) == rows
    assert ("truncated" in res.stderr) is truncated
    assert (f"({rows} of >{rows} rows)" in res.stdout) is truncated


def test_one_truncated_row_is_still_rows(hsql: Hsql, duck: list[str]) -> None:
    """`-l 1` is where the count and the noun disagree.

    One row was kept, and the limit+1 fetch proved there is another, so the
    total the noun has to agree with is at least two.
    """
    res = hsql(*duck, "--limit", "1", "-c", TEN_ROWS)
    assert "(1 of >1 rows)" in res.stdout


def test_no_limit_counts_exactly(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "--limit", "-1", "--stats", "--format", "none", "-c", TEN_ROWS)
    payload = json.loads(res.stderr.splitlines()[-1])
    assert payload["rows"] == 10
    assert payload["truncated"] is False
    assert payload["limit"] is None


def test_limit_zero_fetches_a_header_and_no_rows(
    hsql: Hsql, both_adapters: list[str]
) -> None:
    """`limit 0` is how a caller asks what a query's columns are.

    So 0 is zero rows, and -1 is the spelling for "all of them" -- the reverse
    would spend the idiom on a synonym for a flag that already exists.
    """
    res = hsql(*both_adapters, "--limit", "0", "-c", TEN_ROWS)
    assert res.exit_code == ExitCode.OK
    lines = res.stdout.splitlines()
    assert lines[0].strip() == "n"
    assert lines[-1] == "(0 of >0 rows)"


@pytest.mark.parametrize("flag", ["-t", "--no-footer"])
def test_the_truncation_notice_survives_a_suppressed_footer(
    hsql: Hsql, duck: list[str], flag: str
) -> None:
    """These suppress stdout chrome. They do not suppress a warning.

    The footer is where a truncated result says `3 of >3`, so with it gone the
    stderr note is the only thing that says so at all.
    """
    res = hsql(*duck, flag, "--limit", "3", "-c", TEN_ROWS)
    assert "of >3" not in res.stdout
    assert "results truncated at --limit 3" in res.stderr


# --- the row cap the layouts print under -------------------------------------


@pytest.mark.parametrize(
    ("format_name", "expected"), [("table", 40), ("markdown", 40), ("vertical", 10)]
)
def test_each_layout_has_its_own_default_cap(
    hsql: Hsql, duck: list[str], format_name: str, expected: int
) -> None:
    """A screen holds ten records vertically and forty rows as a table."""
    res = hsql(*duck, "-tA", "--format", format_name, "-c", HUNDRED_ROWS)
    assert res.exit_code == ExitCode.OK
    printed = [line for line in res.stdout.splitlines() if line]
    assert len(printed) == expected


def test_the_footer_says_what_was_not_printed(hsql: Hsql, duck: list[str]) -> None:
    """The rows were fetched, so unlike a hard limit this total is exact."""
    res = hsql(*duck, "-c", HUNDRED_ROWS)
    assert res.stdout.splitlines()[-1] == "(40 of 100 rows)"
    assert res.stderr == ""


def test_both_caps_at_once_keep_their_own_meanings(hsql: Hsql, duck: list[str]) -> None:
    """Fifty fetched of an unknown number, forty printed of the fifty."""
    res = hsql(*duck, "--limit", "50", "-c", HUNDRED_ROWS)
    assert res.stdout.splitlines()[-1] == "(40 of >50 rows)"


@pytest.mark.parametrize("value,expected", [("5", 5), ("-1", 100), ("0", 0)])
def test_display_rows_sets_the_cap(
    hsql: Hsql, duck: list[str], value: str, expected: int
) -> None:
    res = hsql(
        *duck, "-tA", "--limit", "-1", "--display-rows", value, "-c", HUNDRED_ROWS
    )
    assert res.exit_code == ExitCode.OK
    assert len([line for line in res.stdout.splitlines() if line]) == expected


def test_the_cap_does_not_reach_a_file_format(hsql: Hsql, duck: list[str]) -> None:
    """A csv is written for a machine; dropping rows out of it is a different
    promise from not filling a screen with them."""
    res = hsql(*duck, "--csv", "-c", HUNDRED_ROWS)
    assert len(res.stdout.splitlines()) == 101  # header and every row
    assert res.stderr == ""


def test_a_cap_a_file_format_cannot_honor_says_so(hsql: Hsql, duck: list[str]) -> None:
    """Asked for five rows, given a hundred: silence would read as five."""
    res = hsql(*duck, "--csv", "--display-rows", "5", "-c", HUNDRED_ROWS)
    assert len(res.stdout.splitlines()) == 101
    assert "--display-rows" in res.stderr
    assert "csv" in res.stderr


@pytest.mark.parametrize("flag", ["-t", "--no-footer"])
def test_a_suppressed_footer_moves_the_cap_notice_to_stderr(
    hsql: Hsql, duck: list[str], flag: str
) -> None:
    """The footer is the only thing on stdout that says rows were dropped."""
    res = hsql(*duck, flag, "-c", HUNDRED_ROWS)
    assert " 40" in res.stdout and " 41" not in res.stdout
    assert "rows)" not in res.stdout
    assert "printed 40 of 100 rows" in res.stderr


def test_the_footer_is_not_restated_on_stderr(hsql: Hsql, duck: list[str]) -> None:
    """`40 of 100 rows` is already under the result; stderr adds nothing."""
    res = hsql(*duck, "-c", HUNDRED_ROWS)
    assert "printed" not in res.stderr


def test_a_profile_can_set_the_cap(hsql: Hsql, tmp_path: Path) -> None:
    path = tmp_path / ".harlequin.toml"
    path.write_text(
        "default_profile = 'duck'\n"
        "\n[profiles.duck]\n"
        "adapter = 'duckdb'\n"
        "conn_str = [ ':memory:' ]\n"
        "no_init = true\n"
        "display_rows = 3\n"
    )
    res = hsql("--config-path", str(path), "-tA", "-c", HUNDRED_ROWS)
    assert res.stdout == "1\n2\n3\n"


def test_diagnostics_follow_the_data_they_describe(tmp_path: Path) -> None:
    """stdout is block-buffered when it is a pipe; stderr is not.

    Without a flush between them a note overtakes the result set it is about,
    and `hsql ... 2>&1 | less` reads in an order the terminal never showed. Only
    a real subprocess can prove it: in-process, `CliRunner`'s stdout is not a
    pipe and so is not buffered the way the shipped command's is.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from harlequin.hsql import main; sys.argv = sys.argv[1:]; "
            "main()",
            "hsql",
            *["-a", "duckdb", "--no-init", ":memory:"],
            *["--limit", "3", "--stats", "-c", TEN_ROWS],
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=tmp_path,  # out of the repo, whose own .harlequin.toml would apply
    )
    assert proc.returncode == ExitCode.OK, proc.stdout
    combined = proc.stdout
    assert combined.index("(3 of >3 rows)") < combined.index("results truncated")
    assert combined.index("results truncated") < combined.index('"status":"ok"')


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
    written = hsql(*duck, "--format", format_name, "-o", str(destination), "-c", sql)
    piped = hsql(*duck, "--format", format_name, "-c", sql)
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
        "viewer_max_rows = 7\n"
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
    # `limit` is the one both commands honor; `viewer_max_rows = 7` is the
    # IDE's cap on its own widget, and means nothing here.
    assert res.stdout == "".join(f"{n}\n" for n in range(1, 4)), "profile limit of 3"


def test_a_cli_option_beats_the_profile(hsql: Hsql, config_file: Path) -> None:
    res = hsql("--config-path", str(config_file), "-tA", "--limit", "2", "-c", TEN_ROWS)
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


def test_an_option_the_adapter_never_declared_is_a_usage_error(
    hsql: Hsql, tmp_path: Path
) -> None:
    """Before the connection, and before the query it would have run.

    The adapter's own constructor takes supersets of what it declares, so this
    is the only place a misspelling can be caught -- and a run that believed it
    was read-only should not reach the database at all.
    """
    path = tmp_path / ".harlequin.toml"
    path.write_text(
        "[profiles.duck]\n"
        "adapter = 'duckdb'\n"
        "conn_str = [ ':memory:' ]\n"
        "reed_only = true\n"
    )
    res = hsql("--config-path", str(path), "-P", "duck", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "reed_only" in res.stderr
    assert "read_only" in res.stderr
    assert "duckdb" in res.stderr


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


# --- pointing back at the IDE ------------------------------------------------


def test_a_theme_name_after_tuples_only_is_explained(
    hsql: Hsql, duck: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`-t nord` is the one habit the two commands can silently disagree on.

    In a tmp cwd because it succeeds: DuckDB creates a database file named
    `nord` rather than refusing, which is the whole reason this is worth a note.
    """
    monkeypatch.chdir(tmp_path)
    res = hsql("-a", "duckdb", "--no-init", "-t", "nord", "-c", "select 1")
    assert res.exit_code == ExitCode.OK
    assert res.stdout.strip() == "1"
    assert "hsql has no themes" not in res.stdout
    assert "hsql has no themes" in res.stderr
    assert "'nord' was read as a connection string" in res.stderr


def test_the_theme_hint_needs_both_halves(
    hsql: Hsql, duck: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A theme name alone is a database, and `-t` alone is just psql's flag."""
    monkeypatch.chdir(tmp_path)
    without_dash_t = hsql("-a", "duckdb", "--no-init", "nord", "-c", "select 1")
    assert "hsql has no themes" not in without_dash_t.stderr

    without_a_theme = hsql(*duck, "-t", "-c", "select 1")
    assert "hsql has no themes" not in without_a_theme.stderr


def test_the_theme_names_are_the_ides() -> None:
    """The copy in `diagnostics` cannot import the original: it is Textual's.

    So this is the seam where a Textual upgrade that adds a theme shows up.
    """
    from harlequin.colors import VALID_THEMES

    assert IDE_THEMES == frozenset(VALID_THEMES)


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


# --- how the command gets built ----------------------------------------------


def test_the_config_is_read_once(
    hsql: Hsql, duck: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both phases want the profile; only one of them may go to disk for it."""
    import harlequin.config

    reads: list[object] = []
    real = harlequin.config.load_profile

    def counting(config_path: Any, profile_name: Any) -> Any:
        reads.append(config_path)
        return real(config_path=config_path, profile_name=profile_name)

    monkeypatch.setattr("harlequin.hsql.cli.load_profile", counting)
    res = hsql(*duck, "-c", "select 1")
    assert res.exit_code == ExitCode.OK
    assert len(reads) == 1


def test_hsql_does_not_claim_dash_h() -> None:
    """`-h` belongs to `--host`.

    psql spells it that way, and the postgres and mysql adapters both declare
    it. Taking it for help would silently strip it from them.
    """
    cmd = build_cli(["--help"])
    with click.Context(cmd) as ctx:
        opts = {opt for param in cmd.get_params(ctx) for opt in param.opts}
    assert "--help" in opts
    assert "-h" not in opts


def test_an_adapter_option_cannot_shadow_an_hsql_flag() -> None:
    from harlequin.adapter import HarlequinAdapter
    from harlequin.hsql.cli import _attach_adapter_options
    from harlequin.options import TextOption

    class _Adapter:
        ADAPTER_OPTIONS = [
            TextOption(name="collides", description="x", short_decls=["-c"]),
            TextOption(name="format", description="x"),
            TextOption(name="fine", description="x", short_decls=["-Z"]),
        ]

    cmd = build_cli(["--help"])
    _attach_adapter_options(cmd, cast("type[HarlequinAdapter]", _Adapter))
    by_name: dict[str | None, list[click.Parameter]] = {}
    for param in cmd.params:
        by_name.setdefault(param.name, []).append(param)

    # the colliding short goes; the option survives under its long spelling
    (collides,) = by_name["collides"]
    assert collides.opts == ["--collides"]
    # an option whose only spelling hsql already owns is dropped whole, so the
    # command is left with hsql's own --format and no duplicate
    (fmt,) = by_name["format"]
    assert fmt.opts == ["--format"]
    # anything that doesn't collide arrives untouched
    (fine,) = by_name["fine"]
    assert set(fine.opts) == {"--fine", "-Z"}


# --- `--config`, the mode that reports on config files -----------------------


@pytest.fixture
def config_dirs(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """A cwd and a home directory to write config files into, and nothing else.

    Returned nearest first, which is the order the files in them are read, and
    so the order everything below asserts about.
    """
    mock_cwd = tmp_path_factory.mktemp("cwd")
    mock_home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(
        "harlequin.config._search_directories",
        lambda: [
            (mock_cwd, (".harlequin.toml",)),
            (mock_home, (".harlequin.toml",)),
        ],
    )
    return mock_cwd, mock_home


@pytest.fixture
def two_config_files(config_dirs: tuple[Path, Path]) -> tuple[Path, Path]:
    """A home file and a project file that disagree about one profile.

    The shape every question about the merge is asked in: `shared` is defined
    twice, `personal` and `project` once each, and the default is the home
    file's.
    """
    cwd, home = config_dirs
    (home / ".harlequin.toml").write_text(
        'default_profile = "personal"\n'
        "[profiles.personal]\n"
        'adapter = "duckdb"\n'
        "limit = 200000\n"
        "[profiles.shared]\n"
        'adapter = "sqlite"\n'
    )
    (cwd / ".harlequin.toml").write_text(
        "[profiles.shared]\n"
        'adapter = "duckdb"\n'
        "[profiles.project]\n"
        'adapter = "sqlite"\n'
    )
    return cwd / ".harlequin.toml", home / ".harlequin.toml"


def test_config_show_names_the_file_every_value_came_from(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """The single best troubleshooting artifact in the command.

    Asserted whole rather than by substring: what makes this worth having is
    that a reader can see the merge, so the arrangement is the contract.
    """
    project, home = two_config_files
    res = hsql("--config", "show")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == (
        f'default_profile = "personal" # from {home}\n'
        "\n"
        f"[profiles.personal] # from {home}\n"
        'adapter = "duckdb"\n'
        "limit = 200000\n"
        "\n"
        f"[profiles.project] # from {project}\n"
        'adapter = "sqlite"\n'
        "\n"
        f"[profiles.shared] # from {project}, overriding {home}\n"
        'adapter = "duckdb"\n'
    )


def test_config_show_writes_toml_a_parser_reads_back(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """What it prints is the language it is about.

    A document that says `# from ...` and cannot be parsed would be a report
    about TOML rather than TOML, and the whole point of the spelling is that a
    reader can paste a table of it into the file they are fixing.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib

    res = hsql("--config", "show")
    assert tomllib.loads(res.stdout) == {
        "default_profile": "personal",
        "profiles": {
            "personal": {"adapter": "duckdb", "limit": 200000},
            "project": {"adapter": "sqlite"},
            "shared": {"adapter": "duckdb"},
        },
    }


def test_config_show_writes_every_toml_type_back(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """A profile holds whatever TOML holds, and this has to write all of it."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib

    cwd, _ = config_dirs
    written = (
        "[profiles.everything]\n"
        'text = "a \\"quoted\\" \\u00e9"\n'
        "number = 5432\n"
        "fraction = 1.5\n"
        "flag = true\n"
        'list = ["httpfs", "spatial"]\n'
        'table = { host = "localhost", port = 5432 }\n'
        "moment = 2026-08-18T09:30:00\n"
        '"dotted.key" = "quoted"\n'
    )
    (cwd / ".harlequin.toml").write_text(written, encoding="utf-8")

    res = hsql("--config", "show")
    assert res.exit_code == ExitCode.OK
    # the é survives as itself: a config file is UTF-8, and an escape would be
    # something the reader has to decode to recognize their own value
    assert "é" in res.stdout
    assert tomllib.loads(res.stdout)["profiles"] == tomllib.loads(written)["profiles"]


def test_config_show_json_carries_the_same_provenance(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    project, home = two_config_files
    res = hsql("--config", "show", "--json")
    assert res.exit_code == ExitCode.OK
    assert json.loads(res.stdout) == {
        "default_profile": {
            "value": "personal",
            "from": str(home),
            "overrode": [],
        },
        "profiles": {
            "personal": {
                "value": {"adapter": "duckdb", "limit": 200000},
                "from": str(home),
                "overrode": [],
            },
            "project": {
                "value": {"adapter": "sqlite"},
                "from": str(project),
                "overrode": [],
            },
            "shared": {
                "value": {"adapter": "duckdb"},
                "from": str(project),
                "overrode": [str(home)],
            },
        },
        "keymaps": {},
    }


def test_config_show_json_keeps_its_shape_with_nothing_to_report(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """A key nothing defines is null, not a key that is missing.

    A caller that has to branch on whether a key is there is a caller writing
    the same three lines hsql could have written once.
    """
    res = hsql("--config", "show", "--json")
    assert res.exit_code == ExitCode.OK
    assert json.loads(res.stdout) == {
        "default_profile": None,
        "profiles": {},
        "keymaps": {},
    }


def test_config_show_says_so_when_there_is_nothing_to_show(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    res = hsql("--config", "show")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "# No config file defines anything hsql reads.\n"


@pytest.mark.parametrize("argv", [["--csv"], ["--format", "markdown"]])
def test_config_show_notes_a_format_it_cannot_reach(
    hsql: Hsql, two_config_files: tuple[Path, Path], argv: list[str]
) -> None:
    """A document is not rows, and silence would read as a format that applied."""
    res = hsql("--config", "show", *argv)
    assert res.exit_code == ExitCode.OK
    assert res.stdout.startswith("default_profile")
    assert "had no effect" in res.stderr
    assert "--format json" in res.stderr


def test_config_show_writes_nothing_under_format_none(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    res = hsql("--config", "show", "--format", "none")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""


def test_config_show_goes_to_the_file_dash_o_names(
    hsql: Hsql, two_config_files: tuple[Path, Path], tmp_path: Path
) -> None:
    destination = tmp_path / "effective.toml"
    res = hsql("--config", "show", "-o", str(destination))
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert destination.read_text(encoding="utf-8").startswith("default_profile")


def test_config_list_profiles_is_rows(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """The question a human asks first: what can I pass to -P.

    Sorted by name, and the default is a column rather than a decoration on
    one -- a marker inside the name is a name a shell loop would pass back
    wrong.
    """
    res = hsql("--config", "list-profiles")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == (
        " profile  | adapter | default\n"
        "----------+---------+---------\n"
        " personal | duckdb  | true\n"
        " project  | sqlite  | false\n"
        " shared   | duckdb  | false\n"
        "(3 rows)\n"
    )


def test_config_list_profiles_under_the_psql_switches(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """A listing is a result set, so every flag that shapes one reaches it."""
    res = hsql("--config", "list-profiles", "-tA")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == (
        "personal|duckdb|true\nproject|sqlite|false\nshared|duckdb|false\n"
    )


def test_config_list_profiles_as_csv(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    res = hsql("--config", "list-profiles", "--csv", "-t")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == (
        "personal,duckdb,true\nproject,sqlite,false\nshared,duckdb,false\n"
    )


def test_config_list_profiles_leaves_an_unnamed_adapter_null(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """It reports what the files say. A profile that names no adapter is one
    `-a` still decides, and printing hsql's default here would claim the file
    had made a choice it did not make."""
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text("[profiles.bare]\nlimit = 10\n")
    res = hsql("--config", "list-profiles", "-tA")
    assert res.stdout == "bare|NULL|false\n"


def test_config_list_profiles_marks_no_default_when_there_is_none(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text('[profiles.one]\nadapter = "sqlite"\n')
    res = hsql("--config", "list-profiles", "-tA")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "one|sqlite|false\n"


def test_config_list_profiles_reports_a_default_that_names_nothing(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """Not an error, and not silence.

    A `default_profile` naming nothing only stops an invocation that was going
    to use it, and this one is not -- but a short list of names is exactly
    where the mistake is visible, so it is said on stderr and the list still
    prints.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        'default_profile = "gone"\n[profiles.here]\nadapter = "sqlite"\n'
    )
    res = hsql("--config", "list-profiles", "-tA")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "here|sqlite|false\n"
    assert "default_profile is 'gone'" in res.stderr


def test_config_list_profiles_finds_none(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    res = hsql("--config", "list-profiles")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == (
        " profile | adapter | default\n---------+---------+---------\n(0 rows)\n"
    )


def test_config_show_reads_a_profile_it_was_not_asked_about(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """`-P` names a profile to connect with, and this mode connects with none.

    A `-P` that resolves to nothing is a hard error for a run and irrelevant
    here, which is the point: the mode that tells you what your profiles are
    must not refuse to run because one of them is missing.
    """
    res = hsql("--config", "list-profiles", "-P", "no-such-profile", "-tA")
    assert res.exit_code == ExitCode.OK
    assert res.stdout.startswith("personal|")


def test_config_reports_a_file_it_cannot_read(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """`show` reads every file, so a broken one is fatal to it.

    `--config validate` is the mode that reports every problem at once; this
    one stops at the first, and names the file either way.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text("this is not toml\n")
    res = hsql("--config", "show")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert str(cwd / ".harlequin.toml") in res.stderr


@pytest.mark.parametrize("sql", [["-c", "select 1"], ["-f", "-"]])
def test_config_refuses_to_also_run_sql(
    hsql: Hsql, two_config_files: tuple[Path, Path], sql: list[str]
) -> None:
    """Two questions spelled as one invocation, and neither is the answer.

    Showing the config and ignoring the SQL would be the worse outcome of the
    two: a script that thought it ran a query would carry on believing it.
    """
    res = hsql("--config", "show", *sql)
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "does not run SQL" in res.stderr


def test_an_unknown_config_mode_lists_the_ones_that_work(hsql: Hsql) -> None:
    res = hsql("--config", "init")
    assert res.exit_code == ExitCode.USAGE
    assert "show" in res.stderr
    assert "list-profiles" in res.stderr
    assert "validate" in res.stderr
    assert "schema" in res.stderr


def test_config_modes_are_what_the_help_offers(hsql: Hsql) -> None:
    from harlequin.hsql.modes import CONFIG_MODES

    res = hsql("--help")
    assert res.exit_code == ExitCode.OK
    for mode in CONFIG_MODES:
        assert mode in res.output.replace("\n", "").replace(" ", "")


def test_config_show_writes_a_keymap_as_the_array_it_is(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """A keymap is an array of tables, and its provenance is written once.

    The array comes from one file whole, like a profile, so repeating the
    comment over every binding in it would say the same thing as many times as
    the user has key bindings.
    """
    cwd, home = config_dirs
    (home / ".harlequin.toml").write_text(
        "[[keymaps.arrows]]\n"
        'keys = "ctrl+j"\n'
        'action = "cursor_down"\n'
        "[[keymaps.arrows]]\n"
        'keys = "ctrl+k"\n'
        'action = "cursor_up"\n'
    )
    res = hsql("--config", "show")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == (
        f"[[keymaps.arrows]] # from {home / '.harlequin.toml'}\n"
        'keys = "ctrl+j"\n'
        'action = "cursor_down"\n'
        "\n"
        "[[keymaps.arrows]]\n"
        'keys = "ctrl+k"\n'
        'action = "cursor_up"\n'
    )


# --- `--config validate`, the mode that reports every problem ----------------


def test_config_validate_finds_nothing_wrong_with_a_good_config(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """Zero problems is zero rows, and the code a script actually reads."""
    res = hsql("--config", "validate")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == (
        " file | key | problem | line\n------+-----+---------+------\n(0 rows)\n"
    )


def test_config_validate_exits_two_for_a_config_it_found_a_problem_in(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """The whole mode, for a caller that never reads the rows.

    `--format none` is the spelling that says so out loud: no stdout at all,
    and the answer in the exit code.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        '[profiles.prod]\nadapter = "duckdb"\nreed_only = true\n'
    )
    res = hsql("--config", "validate", "--format", "none")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""


def test_config_validate_names_the_file_the_key_and_the_option(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """Pass 2, which is the surface nothing else in the stack validates.

    `reed_only = true` reaches the adapter's constructor and is dropped there
    in silence, leaving a caller who believes they are connected read-only.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        '[profiles.prod]\nadapter = "duckdb"\nreed_only = true\n'
    )
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.USAGE
    file, key, problem, line = res.stdout.strip().split("|")
    assert file == str(cwd / ".harlequin.toml")
    assert key == "profiles.prod.reed_only"
    assert "duckdb" in problem
    assert "read_only" in problem  # the spelling it was probably meant to be
    assert line == "NULL"


def test_config_validate_reports_every_file_and_keeps_reading(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """The mode's whole reason to exist, in the shape it is worst at.

    A file it cannot parse stops `show` dead. Here it is one row, and the file
    behind it is read, validated and reported on anyway -- which is what the
    per-file validation in front of the merge buys.
    """
    cwd, home = config_dirs
    (cwd / ".harlequin.toml").write_text("this is not toml\n")
    (home / ".harlequin.toml").write_text(
        '[profiles.prod]\nadapter = "sqlite"\nreed_only = true\n'
    )
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.USAGE
    first, second = res.stdout.splitlines()
    assert first.startswith(str(cwd / ".harlequin.toml"))
    assert second.startswith(str(home / ".harlequin.toml"))


def test_config_validate_names_the_line_when_the_parser_named_one(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """Where the parser gives a position, and nowhere else.

    A key we merely dislike has no position anywhere, and a number that was not
    read out of a parser would send a reader to a line with nothing on it.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text('[profiles.prod]\nadapter = "duckdb"\noops\n')
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.USAGE
    file, key, _, line = res.stdout.strip().split("|")
    assert file == str(cwd / ".harlequin.toml")
    assert key == "NULL"  # the problem is the file, not something in it
    assert line == "3"


def test_config_validate_reports_a_profile_the_merge_hides(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """One entry per file per problem, which is what validating first buys.

    The nearer file supplies `shared` whole, so no invocation runs the home
    file's copy of it -- and its author will still open that file to fix it.
    """
    cwd, home = config_dirs
    (home / ".harlequin.toml").write_text("[profiles.shared]\nreed_only = true\n")
    (cwd / ".harlequin.toml").write_text("[profiles.shared]\nread_only = true\n")
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout.startswith(
        f"{home / '.harlequin.toml'}|profiles.shared.reed_only"
    )


def test_config_validate_reports_a_default_that_names_nothing(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """Where `list-profiles` notes it, this one fails on it.

    Nothing about the key is more wrong here -- it is the same broken default.
    The difference is what each mode was asked: one for a list of names, and
    this one for whether the config is any good.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        'default_profile = "gone"\n[profiles.here]\nadapter = "sqlite"\n'
    )
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.USAGE
    file, key, problem, _ = res.stdout.strip().split("|")
    assert file == str(cwd / ".harlequin.toml")
    assert key == "default_profile"
    assert "gone" in problem


def test_config_validate_accepts_a_profile_written_for_the_ide(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """Valid is a union of three sets, and getting it wrong breaks working configs.

    One profile serves both commands, so the IDE's keys are as legal here as
    hsql's own and the adapter's -- and a value may arrive uncast, because the
    adapter contract says it may: `port = 5432` and `port = "5432"` are the
    same option written two ways a TOML file invites.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        "[profiles.ide]\n"
        'adapter = "sqlite"\n'  # the adapter's own
        "read_only = true\n"
        "limit = 100\n"  # a key both commands read
        'theme = "nord"\n'  # a key only the IDE reads
        'keymap_name = ["vscode"]\n'
        "[profiles.uncast]\n"
        'adapter = "postgres"\n'
        "port = 5432\n"
    )
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""


def test_config_validate_reports_an_adapter_nothing_installs(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """A profile whose adapter cannot be loaded is one profile's problem.

    It cannot be checked any further -- there are no declared options to check
    it against -- and the file's other profiles still can be.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        '[profiles.gone]\nadapter = "no-such-adapter"\n'
        '[profiles.fine]\nadapter = "sqlite"\nread_only = true\n'
    )
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.USAGE
    (row,) = res.stdout.splitlines()
    assert row.split("|")[1] == "profiles.gone.adapter"
    assert "no-such-adapter" in row


def test_config_validate_reports_a_keymap_the_ide_would_refuse(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """A keymap is checked here rather than when the IDE next starts."""
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        '[[keymaps.mine]]\nkeys = "ctrl+j"\naction = "cursor_down"\noops = 1\n'
    )
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout.split("|")[1] == "keymaps.mine"


def test_config_validate_writes_one_line_per_problem(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """A problem is a row, and a row is a line.

    Some of these messages are written over two lines when they are raised at a
    caller; folded into a cell they would arrive as two rows in every format
    that does not quote its cells.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        '[profiles.prod]\nadapter = "sqlite"\nmode = "reed-only"\n'
    )
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.USAGE
    (row,) = res.stdout.splitlines()
    assert "Allowed values" in row


def test_config_validate_is_json_for_a_caller(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """Four facts per problem, under the same --json as everywhere else."""
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        '[profiles.prod]\nadapter = "duckdb"\nreed_only = true\n'
    )
    res = hsql("--config", "validate", "--json")
    assert res.exit_code == ExitCode.USAGE
    (problem,) = json.loads(res.stdout)
    assert problem["file"] == str(cwd / ".harlequin.toml")
    assert problem["key"] == "profiles.prod.reed_only"
    assert problem["line"] is None
    assert "duckdb" in problem["problem"]


def test_config_validate_says_when_there_was_nothing_to_check(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """A clean config and no config at all read the same on stdout.

    They are not the same answer, and the one that found nothing to check is
    the one a caller would otherwise mistake for a passing check.
    """
    res = hsql("--config", "validate")
    assert res.exit_code == ExitCode.OK
    assert "No config file defines anything" in res.stderr


def test_config_validate_does_not_connect(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """It reads a declaration, not a database.

    A profile pointing at a file that is not there is exactly the config a
    caller runs this mode on, and needing the database to check the config
    would make the mode useless where it is most wanted.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        '[profiles.prod]\nadapter = "sqlite"\nconn_str = ["/no/such/dir/db.sqlite"]\n'
    )
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""


# --- `--config schema`, the mode that reports what may be in a config file ---


def schema_of(res: Result) -> dict[str, Any]:
    assert res.exit_code == ExitCode.OK
    return cast("dict[str, Any]", json.loads(res.stdout))


def test_config_schema_is_a_json_schema(hsql: Hsql) -> None:
    schema = schema_of(hsql("--config", "schema"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["properties"]) == {"default_profile", "profiles", "keymaps"}
    assert schema["$defs"]["profile"]["properties"]["limit"]["type"] == "integer"


def test_config_schema_describes_the_adapters_installed_here(hsql: Hsql) -> None:
    """The reason to generate it rather than ship one: it knows what is here."""
    schema = schema_of(hsql("--config", "schema"))
    installed = schema["$defs"]["profile"]["properties"]["adapter"]["enum"]
    assert {"duckdb", "sqlite"} <= set(installed)
    for name in installed:
        assert f"{name}_options" in schema["$defs"]


def test_config_schema_describes_an_adapters_own_options(hsql: Hsql) -> None:
    options = schema_of(hsql("--config", "schema"))["$defs"]["duckdb_options"]
    assert options["properties"]["read_only"]["type"] == "boolean"
    assert options["properties"]["extension"]["type"] == "array"


def test_config_schema_does_not_claim_the_published_id(hsql: Hsql) -> None:
    """The document published at that URL is the one that names no adapter."""
    assert "$id" not in schema_of(hsql("--config", "schema"))


def test_config_schema_reads_no_config_file(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """It describes what a config file may say, not what one says.

    So it answers over a file that cannot be parsed -- which is a config a
    caller might well be reaching for a schema to fix.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text("[profiles.prod\nadapter = 'duckdb'\n")
    assert schema_of(hsql("--config", "schema"))["title"] == "Harlequin config"


def test_config_schema_does_not_connect(hsql: Hsql) -> None:
    res = hsql("--config", "schema", ":memory:")
    assert res.exit_code == ExitCode.OK
    assert json.loads(res.stdout)


def test_config_schema_does_not_run_sql(hsql: Hsql) -> None:
    res = hsql("--config", "schema", ":memory:", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert "does not run SQL" in res.stderr


def test_config_schema_writes_nothing_under_format_none(hsql: Hsql) -> None:
    res = hsql("--config", "schema", "--format", "none")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""


@pytest.mark.parametrize("argv", [["--csv"], ["--format", "markdown"]])
def test_config_schema_notes_a_format_it_cannot_reach(
    hsql: Hsql, argv: list[str]
) -> None:
    res = hsql("--config", "schema", *argv)
    assert res.exit_code == ExitCode.OK
    assert json.loads(res.stdout)
    assert "--config schema" in res.stderr


def test_config_schema_json_is_not_a_format_it_declines(hsql: Hsql) -> None:
    res = hsql("--config", "schema", "--json")
    assert res.exit_code == ExitCode.OK
    assert res.stderr == ""


def test_config_schema_goes_to_the_file_dash_o_names(
    hsql: Hsql, tmp_path: Path
) -> None:
    destination = tmp_path / "config-schema.json"
    res = hsql("--config", "schema", "-o", str(destination))
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert json.loads(destination.read_text())["title"] == "Harlequin config"


# --- `--spec`, the mode that reports on the command itself -------------------


def spec_of(res: Result) -> dict[str, Any]:
    assert res.exit_code == ExitCode.OK
    return cast("dict[str, Any]", json.loads(res.stdout))


def option_named(spec: dict[str, Any], name: str) -> dict[str, Any]:
    (option,) = [o for o in spec["options"] if o["name"] == name]
    return cast("dict[str, Any]", option)


def test_spec_is_json(hsql: Hsql) -> None:
    spec = spec_of(hsql("--spec"))
    assert spec["program"] == "hsql"
    assert spec["version"]
    assert spec["options"]
    assert spec["adapters"]


def test_spec_describes_an_option_a_caller_would_otherwise_guess_at(
    hsql: Hsql,
) -> None:
    """What `--help` says in prose, as the four facts a caller needs.

    Whether it takes a value, what kind, what happens without it, and how to
    spell it -- an agent that reads this does not have to try `--limit` twice
    to find out it is not a flag.
    """
    limit = option_named(spec_of(hsql("--spec")), "limit")
    assert limit["decls"] == ["--limit"]
    assert limit["type"] == "integer"
    assert limit["default"] == 500
    assert limit["is_flag"] is False
    assert limit["multiple"] is False
    assert limit["choices"] is None
    assert "rows fetched" in limit["help"]


def test_spec_names_the_values_a_choice_takes(hsql: Hsql) -> None:
    """The list `--format` will accept, so nobody has to parse it out of the
    epilog."""
    from harlequin.hsql import output

    fmt = option_named(spec_of(hsql("--spec")), "format")
    assert fmt["type"] == "choice"
    assert fmt["choices"] == list(output.format_names())
    assert fmt["default"] == "table"


def test_spec_reports_no_default_as_null(hsql: Hsql) -> None:
    """An option with no default has none, spelled the way JSON spells nothing.

    click holds "no default" as a sentinel object, and a document that let one
    through would say `--profile` defaults to the string `Sentinel.UNSET`.
    """
    spec = spec_of(hsql("--spec"))
    assert option_named(spec, "profile")["default"] is None
    assert option_named(spec, "display_rows")["default"] is None
    for option in spec["options"]:
        assert option["default"] is None or isinstance(
            option["default"], (str, int, float, bool, list)
        )


def test_spec_reports_every_spelling_of_an_option(hsql: Hsql) -> None:
    tuples_only = option_named(spec_of(hsql("--spec")), "tuples_only")
    assert sorted(tuples_only["decls"]) == ["--tuples-only", "-t"]
    assert tuples_only["is_flag"] is True
    assert tuples_only["default"] is False


def test_spec_reports_a_repeatable_option_as_one(hsql: Hsql) -> None:
    assert option_named(spec_of(hsql("--spec")), "command")["multiple"] is True


def test_spec_reports_an_environment_variable(hsql: Hsql) -> None:
    """A flag that reads the environment reads it whether or not it was typed,
    which makes it exactly the kind of thing a caller wants told rather than
    discovered."""
    config_path = option_named(spec_of(hsql("--spec")), "config_path")
    assert config_path["envvar"] == "HARLEQUIN_CONFIG_PATH"


def test_spec_reports_an_envvar_for_every_option_that_reads_one(hsql: Hsql) -> None:
    """Null for the rest, because there is nothing behind them to report.

    hsql declares one `envvar=` and sets no `auto_envvar_prefix`, so click
    derives nothing: `HSQL_LIMIT` is not read, and a spec that implied it was
    would be worse than one that says null. This is the assertion that fails if
    a prefix is ever set, or an option grows an `envvar=` this does not carry.
    """
    spec = spec_of(hsql("--spec"))
    named = {o["name"]: o["envvar"] for o in spec["options"] if o["envvar"]}
    assert named == {"config_path": "HARLEQUIN_CONFIG_PATH"}
    # `to_click()` passes no `envvar=`, so an adapter option cannot have one
    for adapter in spec["adapters"].values():
        assert all(o["envvar"] is None for o in adapter["options"])


def test_hsql_reads_no_environment_variable_it_did_not_declare(
    hsql: Hsql, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: click reads a variable only where one was declared.

    Setting `auto_envvar_prefix` would make every flag configurable through the
    environment, which is a surface `--spec` would then have to report -- so
    assert the surface is the one the document describes.
    """
    monkeypatch.setenv("HSQL_LIMIT", "3")
    monkeypatch.setenv("HARLEQUIN_LIMIT", "3")
    res = hsql("-a", "duckdb", "--no-init", ":memory:", "-tA", "-c", TEN_ROWS)
    assert res.exit_code == ExitCode.OK
    assert len(res.stdout.splitlines()) == 10


def test_spec_names_the_positional(hsql: Hsql) -> None:
    """`CONN_STR` is where the database goes, and it has no flag to find it by.

    `nargs` of -1 is the half a caller cannot see any other way: one connection
    string or five are both this argument.
    """
    (conn_str,) = spec_of(hsql("--spec"))["arguments"]
    assert conn_str["name"] == "conn_str"
    assert conn_str["metavar"] == "CONN_STR"
    assert conn_str["nargs"] == -1
    assert conn_str["required"] is False


def test_spec_covers_every_installed_adapter(hsql: Hsql) -> None:
    from harlequin.plugins import adapter_names

    spec = spec_of(hsql("--spec"))
    assert sorted(spec["adapters"]) == sorted(adapter_names())
    for adapter in spec["adapters"].values():
        assert adapter["error"] is None
        assert adapter["options"]
        assert adapter["version"]


def test_spec_reports_an_adapter_option_the_way_it_is_passed(hsql: Hsql) -> None:
    """The two spellings an adapter option has, and both are needed.

    `--read-only` is what a command line takes; `read_only` is what a profile
    writes, and what the adapter's constructor is handed. A caller that had
    only one of them would write the other wrong.
    """
    duckdb = spec_of(hsql("--spec"))["adapters"]["duckdb"]
    (read_only,) = [o for o in duckdb["options"] if o["name"] == "read_only"]
    assert read_only["decls"] == ["--read-only", "-readonly", "-r"]
    assert read_only["type"] == "boolean"
    assert read_only["is_flag"] is True
    assert read_only["default"] is False
    assert read_only["help"]


def test_spec_reports_an_adapters_repeatable_and_chosen_options(hsql: Hsql) -> None:
    """One option of each shape, named off the lists both adapters always declare.

    sqlite's `--extension` and `--isolation-level` are appended only where the
    interpreter's sqlite3 supports them, so neither is a name a test may assume
    -- duckdb's `--extension` is in its list unconditionally, and is the
    repeatable one here for that reason.
    """
    spec = spec_of(hsql("--spec"))
    duckdb = {o["name"]: o for o in spec["adapters"]["duckdb"]["options"]}
    sqlite = {o["name"]: o for o in spec["adapters"]["sqlite"]["options"]}
    assert duckdb["extension"]["multiple"] is True
    assert sqlite["mode"]["type"] == "choice"
    assert sqlite["mode"]["choices"]
    assert sqlite["init_path"]["type"] == "path"


@pytest.mark.parametrize("name", ["duckdb", "sqlite"])
def test_spec_reports_what_the_adapter_declares_now(hsql: Hsql, name: str) -> None:
    """The document is built from the declarations, not from a list of names.

    An adapter may declare a different set on a different interpreter -- sqlite
    appends `--extension` only where `enable_load_extension` exists, which is
    not on macOS -- so what `--spec` reports has to follow that, and a test that
    pins names cannot tell the difference between the two.
    """
    from harlequin.config import sluggify_option_name
    from harlequin.plugins import load_adapter

    declared = {
        sluggify_option_name(option.name)
        for option in load_adapter(name).ADAPTER_OPTIONS or []
    }
    reported = {
        o["name"]
        for o in spec_of(hsql("--spec", "-a", name))["adapters"][name]["options"]
    }
    # equal today, because neither in-tree adapter declares a spelling hsql's
    # own flags take; a name that goes missing here is one hsql has claimed
    assert reported == declared


def test_spec_narrows_to_one_adapter(hsql: Hsql) -> None:
    """`-a` is the only thing that changes the document, and it changes one
    half of it: hsql's own options are hsql's own whatever adapter is named."""
    everything = spec_of(hsql("--spec"))
    one = spec_of(hsql("--spec", "-a", "sqlite"))
    assert list(one["adapters"]) == ["sqlite"]
    assert one["adapters"]["sqlite"] == everything["adapters"]["sqlite"]
    assert one["options"] == everything["options"]


def test_spec_says_what_it_does_not_cover(hsql: Hsql) -> None:
    """It cannot describe the IDE's flags -- reading them means importing the
    command that builds them, which hsql may not do -- so it says where it
    stops rather than reading as an exhaustive list that is not one."""
    scope = spec_of(hsql("--spec"))["scope"]
    assert "harlequin" in scope
    assert "not here" in scope


def test_spec_options_are_sorted_by_name(hsql: Hsql) -> None:
    """A document a caller diffs between two machines, or two releases."""
    spec = spec_of(hsql("--spec"))
    assert [o["name"] for o in spec["options"]] == sorted(
        o["name"] for o in spec["options"]
    )
    for adapter in spec["adapters"].values():
        assert [o["name"] for o in adapter["options"]] == sorted(
            o["name"] for o in adapter["options"]
        )


def test_spec_does_not_run_sql(hsql: Hsql) -> None:
    res = hsql("--spec", ":memory:", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "does not run SQL" in res.stderr


def test_spec_carries_no_adapters_options(hsql: Hsql) -> None:
    """It describes the connection options rather than accepting them.

    A mode that connects to nothing has nothing to do with `--no-init`, and
    taking it would be a flag that silently did nothing.
    """
    res = hsql("--spec", "-a", "duckdb", "--no-init")
    assert res.exit_code == ExitCode.USAGE
    assert "--no-init" in res.stderr


def test_two_modes_is_a_usage_error_naming_both(hsql: Hsql) -> None:
    """Modes are options, so nothing about the parse stops a caller passing two
    -- and two questions in one invocation have no answer that is not a guess
    about which was meant."""
    res = hsql("--spec", "--config", "show")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "--config show" in res.stderr
    assert "--spec" in res.stderr


def test_spec_writes_nothing_under_format_none(hsql: Hsql) -> None:
    res = hsql("--spec", "--format", "none")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""


@pytest.mark.parametrize("argv", [["--csv"], ["--format", "markdown"]])
def test_spec_notes_a_format_it_cannot_reach(hsql: Hsql, argv: list[str]) -> None:
    """A document is not rows, and silence would read as a format that applied."""
    res = hsql("--spec", *argv)
    assert res.exit_code == ExitCode.OK
    assert json.loads(res.stdout)["program"] == "hsql"
    assert "had no effect" in res.stderr
    assert "--format json" in res.stderr


def test_spec_json_is_not_a_format_it_declines(hsql: Hsql) -> None:
    res = hsql("--spec", "--json")
    assert res.exit_code == ExitCode.OK
    assert json.loads(res.stdout)["program"] == "hsql"
    assert res.stderr == ""


def test_spec_goes_to_the_file_dash_o_names(hsql: Hsql, tmp_path: Path) -> None:
    destination = tmp_path / "spec.json"
    res = hsql("--spec", "-o", str(destination))
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert json.loads(destination.read_text(encoding="utf-8"))["program"] == "hsql"


def test_spec_answers_over_a_config_it_could_not_read(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """The mode that describes the command does not depend on the config.

    A caller whose config file is broken is one of the callers most likely to
    be reading this, and a spec that refused over a file it never needed would
    be useless exactly there.
    """
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text("this is not toml\n")
    assert spec_of(hsql("--spec"))["program"] == "hsql"


def test_spec_ignores_a_profile_that_is_not_there(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """`-P` names a profile to connect with, and this mode connects with none."""
    assert spec_of(hsql("--spec", "-P", "no-such-profile"))["program"] == "hsql"


def test_spec_reports_an_adapter_it_could_not_import(
    hsql: Hsql, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An adapter that is installed and will not import is a fact about the
    installation, not a reason to answer nothing about the rest of it."""
    from harlequin.exception import HarlequinConfigError
    from harlequin.plugins import load_adapter as real_load_adapter

    def fake_load_adapter(name: str) -> Any:
        if name == "duckdb":
            raise HarlequinConfigError("No module named 'duckdb'", title="nope")
        return real_load_adapter(name)

    monkeypatch.setattr("harlequin.plugins.load_adapter", fake_load_adapter)
    res = hsql("--spec")
    spec = spec_of(res)
    assert spec["adapters"]["duckdb"]["options"] is None
    assert "duckdb" in spec["adapters"]["duckdb"]["error"]
    assert spec["adapters"]["sqlite"]["options"]
    assert "could not be imported" in res.stderr


@pytest.mark.parametrize("argv", [["--help"], ["--help", "-a", "duckdb"]])
def test_the_help_points_at_the_machine_readable_one(
    hsql: Hsql, argv: list[str]
) -> None:
    """`--help` names adapters and `--spec` fills their options in.

    That is the trade the epilog makes -- a list of names rather than four
    option tables -- so the end of the help is where a reader who wanted the
    tables should be told where they are. Both spellings of the help say it:
    `-a duckdb` answers for one adapter, and the JSON is still how you get all
    of them.
    """
    res = hsql(*argv)
    assert res.exit_code == ExitCode.OK
    assert f"{PROGRAM} --spec" in res.output
    assert "Machine-readable:" in res.output


def test_spec_is_in_the_help(hsql: Hsql) -> None:
    res = hsql("--help")
    assert res.exit_code == ExitCode.OK
    assert "--spec" in res.output


def test_spec_lists_the_flag_that_would_have_shown_the_help(hsql: Hsql) -> None:
    """click keeps `--help` out of a command's params and adds it at parse
    time, so a document built from the params alone would omit the one flag
    every caller already knows."""
    assert option_named(spec_of(hsql("--spec")), "help")["decls"] == ["--help"]


def test_spec_drops_a_spelling_an_hsql_flag_already_owns(
    hsql: Hsql, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It reports the surface a caller can type, not the one an adapter wanted.

    hsql's own flags win a collision (`_attach_adapter_options`), so an adapter
    option declaring `-c` does not get it — and a spec that listed it anyway
    would send a caller to write `hsql -c` and mean something else.
    """
    from harlequin.options import FlagOption, TextOption
    from harlequin.plugins import load_adapter as real_load_adapter

    shadowed = TextOption(name="command", description="Shadowed by hsql's -c.")
    partly = FlagOption(
        name="cautious", description="Keeps its long name.", short_decls=["-c", "-w"]
    )

    def fake_load_adapter(name: str) -> Any:
        adapter = real_load_adapter(name)
        if name != "sqlite":
            return adapter
        return type("Faked", (adapter,), {"ADAPTER_OPTIONS": [shadowed, partly]})

    monkeypatch.setattr("harlequin.plugins.load_adapter", fake_load_adapter)
    options = spec_of(hsql("--spec", "-a", "sqlite"))["adapters"]["sqlite"]["options"]
    # `command` is gone whole: hsql already has a parameter by that name
    assert [o["name"] for o in options] == ["cautious"]
    # and `-c` is gone from the one that kept its other spellings
    assert options[0]["decls"] == ["--cautious", "-w"]


# --- `--info`, the mode that reports on the installation ---------------------


def info_of(res: Result) -> dict[str, Any]:
    assert res.exit_code == ExitCode.OK
    return cast("dict[str, Any]", json.loads(res.stdout))


def test_info_is_json(hsql: Hsql) -> None:
    info = info_of(hsql("--info"))
    assert info["program"] == "hsql"
    assert info["version"]
    assert info["python"]["version"]
    assert info["python"]["implementation"]
    assert info["python"]["executable"]
    assert info["platform"]["system"]
    assert info["adapters"]


def test_info_reports_the_interpreter_it_is_running_under(hsql: Hsql) -> None:
    """The half of a bug report nobody remembers to include."""
    import platform

    info = info_of(hsql("--info"))
    assert info["python"]["version"] == platform.python_version()
    assert info["python"]["executable"] == sys.executable
    assert info["platform"]["machine"] == platform.machine()


def test_info_lists_config_files_in_precedence_order(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """Nearest first, which is the order that decides which file wins."""
    project, home = two_config_files
    info = info_of(hsql("--info"))
    assert info["config"] == {
        "path": None,
        "files": [str(project), str(home)],
    }


def test_info_lists_no_config_files_when_there_are_none(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    assert info_of(hsql("--info"))["config"]["files"] == []


def test_info_names_the_file_config_path_pointed_at(hsql: Hsql, tmp_path: Path) -> None:
    """An explicit file is the only one read, and it is the one to report."""
    path = tmp_path / "explicit.toml"
    path.write_text('[profiles.here]\nadapter = "sqlite"\n')
    config = info_of(hsql("--info", "--config-path", str(path)))["config"]
    assert config["path"] == str(path)
    assert config["files"] == [str(path)]


def test_info_names_the_active_profile_and_what_chose_the_adapter(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """Which profile a run would use, and why it would connect where it does."""
    info = info_of(hsql("--info"))
    assert info["profile"] == {
        "name": "personal",
        "options": {"adapter": "duckdb", "limit": 200000},
        "error": None,
    }
    assert info["adapter"] == {"name": "duckdb", "from": "profile"}


def test_info_reports_the_profile_dash_p_asked_for(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    info = info_of(hsql("--info", "-P", "project"))
    assert info["profile"]["name"] == "project"
    assert info["adapter"] == {"name": "sqlite", "from": "profile"}


def test_info_says_dash_a_beat_the_profile(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """`-a` wins over the profile, and a report that did not say so would send
    a reader to the wrong file to change it."""
    assert info_of(hsql("--info", "-a", "sqlite"))["adapter"] == {
        "name": "sqlite",
        "from": "-a",
    }


def test_info_falls_back_to_the_default_adapter(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    from harlequin.config import DEFAULT_ADAPTER

    assert info_of(hsql("--info"))["adapter"] == {
        "name": DEFAULT_ADAPTER,
        "from": "default",
    }


def test_info_reports_a_profile_no_file_defines(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """A `-P` typo is what this mode is most often run to find, so it is an
    answer rather than a refusal -- and the answer a run would have refused
    with."""
    info = info_of(hsql("--info", "-P", "prod"))
    assert info["profile"]["name"] == "prod"
    assert info["profile"]["options"] is None
    assert "prod" in info["profile"]["error"]
    # the same message a run gives, because it comes from the same resolution
    res = hsql("-P", "prod", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert info["profile"]["error"] in res.stderr


def test_info_reports_a_default_profile_that_names_nothing(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        'default_profile = "gone"\n[profiles.here]\nadapter = "sqlite"\n'
    )
    info = info_of(hsql("--info"))
    assert info["profile"]["options"] is None
    assert "default_profile" in info["profile"]["error"]
    assert "gone" in info["profile"]["error"]


def test_info_reads_no_further_than_a_run_would(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """It reports the profile a run would use, so it stops where a run stops.

    A file behind the one that defines the profile is never opened, so its
    contents cannot turn this into a report about a problem no run would meet.
    `--config validate` is the mode that reads every file.
    """
    cwd, home = config_dirs
    (cwd / ".harlequin.toml").write_text(
        'default_profile = "near"\n[profiles.near]\nadapter = "sqlite"\n'
    )
    (home / ".harlequin.toml").write_text("this is not toml\n")

    info = info_of(hsql("--info"))
    assert info["profile"] == {
        "name": "near",
        "options": {"adapter": "sqlite"},
        "error": None,
    }
    # discovery still names it: it is a file on disk that a run may yet read
    assert info["config"]["files"] == [
        str(cwd / ".harlequin.toml"),
        str(home / ".harlequin.toml"),
    ]


def test_info_reports_the_profile_named_none_as_the_defaults(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    info = info_of(hsql("--info", "-P", "None"))
    assert info["profile"] == {"name": "None", "options": {}, "error": None}


def test_info_answers_over_a_config_it_could_not_read(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """A caller whose config file is broken is the caller most likely to be
    running this, so the parse error is part of the answer."""
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text("this is not toml\n")
    info = info_of(hsql("--info"))
    assert info["config"]["files"] == [str(cwd / ".harlequin.toml")]
    assert info["profile"]["options"] is None
    assert "line 1" in info["profile"]["error"]
    assert info["adapters"]


def test_info_reports_declared_capabilities_for_every_installed_adapter(
    hsql: Hsql,
) -> None:
    from importlib.metadata import version

    from harlequin.plugins import adapter_names

    info = info_of(hsql("--info"))
    assert sorted(info["adapters"]) == sorted(adapter_names())
    for entry in info["adapters"].values():
        assert entry["error"] is None
        assert entry["capabilities"]["implements_cancel"] in (True, False)
    duckdb = info["adapters"]["duckdb"]
    assert duckdb["distribution"] == "harlequin"
    assert duckdb["version"] == version("harlequin")


def test_info_reads_the_capability_names_off_the_contract(hsql: Hsql) -> None:
    """A capability added to `HarlequinAdapter` is reported without this mode
    keeping a second list of them."""
    from harlequin.adapter import HarlequinAdapter

    declared = sorted(
        name.lower()
        for name in vars(HarlequinAdapter)
        if name.startswith("IMPLEMENTS_")
    )
    assert declared  # the contract declares at least one
    capabilities = info_of(hsql("--info"))["adapters"]["duckdb"]["capabilities"]
    assert sorted(capabilities) == declared


def test_info_narrows_to_one_adapter(hsql: Hsql) -> None:
    assert list(info_of(hsql("--info", "-a", "sqlite"))["adapters"]) == ["sqlite"]


def test_info_reports_an_adapter_it_could_not_import_as_unknown(
    hsql: Hsql, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never `false`: guessing false about what an adapter implements is the
    direction that gets someone hurt."""
    from harlequin.plugins import load_adapter as real_load_adapter

    def fake_load_adapter(name: str) -> Any:
        if name == "duckdb":
            raise HarlequinConfigError("No module named 'duckdb'", title="nope")
        return real_load_adapter(name)

    monkeypatch.setattr("harlequin.plugins.load_adapter", fake_load_adapter)
    res = hsql("--info")
    info = info_of(res)
    assert info["adapters"]["duckdb"]["capabilities"] == "unknown"
    assert "duckdb" in info["adapters"]["duckdb"]["error"]
    # and the rest of the installation is still reported
    assert info["adapters"]["sqlite"]["capabilities"]["implements_cancel"] is True
    assert "could not be imported" in res.stderr


def test_info_opens_no_connection(hsql: Hsql, monkeypatch: pytest.MonkeyPatch) -> None:
    """The design decision this mode exists on: the diagnostic a caller runs
    when the database is unreachable must not itself require the database."""
    from harlequin.plugins import load_adapter as real_load_adapter

    def refuse(self: Any) -> Any:
        raise AssertionError("--info opened a connection")

    def fake_load_adapter(name: str) -> Any:
        return type("Faked", (real_load_adapter(name),), {"connect": refuse})

    monkeypatch.setattr("harlequin.plugins.load_adapter", fake_load_adapter)
    assert info_of(hsql("--info"))["adapters"]["duckdb"]["capabilities"]


def test_info_does_not_run_sql(hsql: Hsql) -> None:
    res = hsql("--info", ":memory:", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "does not run SQL" in res.stderr


def test_info_carries_no_adapters_options(hsql: Hsql) -> None:
    """It reports what an adapter declares rather than taking its options."""
    res = hsql("--info", "-a", "duckdb", "--no-init")
    assert res.exit_code == ExitCode.USAGE
    assert "--no-init" in res.stderr


@pytest.mark.parametrize("other", [["--spec"], ["--config", "show"]])
def test_info_beside_another_mode_is_a_usage_error(
    hsql: Hsql, other: list[str]
) -> None:
    res = hsql("--info", *other)
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "--info" in res.stderr


def test_info_writes_nothing_under_format_none(hsql: Hsql) -> None:
    res = hsql("--info", "--format", "none")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""


@pytest.mark.parametrize("argv", [["--csv"], ["--format", "markdown"]])
def test_info_notes_a_format_it_cannot_reach(hsql: Hsql, argv: list[str]) -> None:
    res = hsql("--info", *argv)
    assert res.exit_code == ExitCode.OK
    assert json.loads(res.stdout)["program"] == "hsql"
    assert "had no effect" in res.stderr
    assert "--format json" in res.stderr


def test_info_json_is_not_a_format_it_declines(hsql: Hsql) -> None:
    res = hsql("--info", "--json")
    assert res.exit_code == ExitCode.OK
    assert json.loads(res.stdout)["program"] == "hsql"
    assert res.stderr == ""


def test_info_goes_to_the_file_dash_o_names(hsql: Hsql, tmp_path: Path) -> None:
    destination = tmp_path / "info.json"
    res = hsql("--info", "-o", str(destination))
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert json.loads(destination.read_text(encoding="utf-8"))["program"] == "hsql"


def test_info_is_in_the_help(hsql: Hsql) -> None:
    res = hsql("--help")
    assert res.exit_code == ExitCode.OK
    assert "--info" in res.output
    assert f"{PROGRAM} --info" in res.output
