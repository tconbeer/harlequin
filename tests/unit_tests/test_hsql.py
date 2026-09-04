"""hsql's contract: what lands on stdout, what lands on stderr, and the code.

The output format and the exit codes are the part of hsql that is an API, so
most of what is asserted here is a promise rather than an implementation: that
stdout carries data and only data, that a truncated result says so, that the
same query renders the same bytes wherever it is run.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Sequence, cast
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner, Result

from harlequin.crash import ISSUE_URL
from harlequin.exception import (
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinCopyError,
    HarlequinQueryError,
)
from harlequin.hsql.cli import PROGRAM, build_cli
from harlequin.hsql.diagnostics import IDE_THEMES, ExitCode, exit_code_for
from harlequin.hsql.modes import CONFIG_MODES

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
    assert "--no-init" not in res.output
    assert "--theme" not in res.output
    assert "--show-files" not in res.output


def test_help_for_one_adapter(hsql: Hsql) -> None:
    res = hsql("--help", "-a", "duckdb")
    assert res.exit_code == ExitCode.OK
    assert "--no-init" in res.output
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
        ("-x", "vertical"),
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


def test_diagnostics_follow_the_data_they_describe(
    tmp_path: Path, clean_env: dict[str, str]
) -> None:
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
        env=clean_env,
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
    assert "-o DIR" in res.stderr


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


def test_a_binary_file_is_a_usage_error(
    hsql: Hsql, duck: list[str], tmp_path: Path
) -> None:
    """A file that isn't text says so, instead of raising a decode error."""
    script = tmp_path / "database.db"
    script.write_bytes(b"\xcd\xe3k.4,9\x97DUCK")
    res = hsql(*duck, "-f", str(script))
    assert res.exit_code == ExitCode.USAGE
    assert "could not read" in res.stderr
    assert "not UTF-8 text" in res.stderr


def test_binary_stdin_is_a_usage_error(hsql: Hsql, duck: list[str]) -> None:
    res = hsql(*duck, "-f", "-", input=b"\xcd\xe3k.4,9\x97DUCK")
    assert res.exit_code == ExitCode.USAGE
    assert "could not read standard input" in res.stderr


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
    in_the_way = tmp_path / "not-a-directory"
    in_the_way.write_text("")
    res = hsql(*duck, "-o", str(in_the_way / "out.csv"), "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stderr.startswith("hsql: error: ")


def test_a_missing_parent_directory_is_created(
    hsql: Hsql, duck: list[str], tmp_path: Path
) -> None:
    destination = tmp_path / "exports" / "nested" / "out.csv"
    res = hsql(*duck, "-o", str(destination), "-tA", "-c", "select 1")
    assert res.exit_code == ExitCode.OK
    assert destination.read_text() == "1\n"


# --- writing to a directory --------------------------------------------------


def test_a_directory_takes_one_file_per_result_set(
    hsql: Hsql, duck: list[str], tmp_path: Path
) -> None:
    """A folder is what lets a format that holds one result set take a script
    that produced several."""
    destination = tmp_path / "exports"
    res = hsql(*duck, "--csv", "-o", str(destination), "-c", "select 1; select 2")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert (destination / "results_1.csv").read_text() == "1\n1\n"
    assert (destination / "results_2.csv").read_text() == "2\n2\n"


def test_a_directory_names_what_it_wrote_on_stderr(
    hsql: Hsql, duck: list[str], tmp_path: Path
) -> None:
    """The caller did not name these files, so stderr says what they are."""
    destination = tmp_path / "exports"
    res = hsql(*duck, "--csv", "-o", str(destination), "-c", "select 1; select 2")
    assert "results_1.csv" in res.stderr
    assert "results_2.csv" in res.stderr


def test_a_named_file_is_not_named_again(
    hsql: Hsql, duck: list[str], tmp_path: Path
) -> None:
    destination = tmp_path / "out.csv"
    res = hsql(*duck, "-o", str(destination), "-c", "select 1")
    assert res.stderr == ""


@pytest.mark.parametrize("format_name", FILE_FORMATS + LAYOUTS)
def test_a_directory_file_holds_what_the_same_format_writes(
    hsql: Hsql, duck: list[str], tmp_path: Path, format_name: str
) -> None:
    destination = tmp_path / "exports"
    hsql(*duck, "--format", format_name, "-o", str(destination), "-c", "select 1 as a")
    (written,) = destination.iterdir()
    piped = hsql(*duck, "--format", format_name, "-c", "select 1 as a")
    assert written.read_bytes() == piped.stdout_bytes


def test_a_directory_is_created_when_it_is_not_there(
    hsql: Hsql, duck: list[str], tmp_path: Path
) -> None:
    destination = tmp_path / "exports" / "nested"
    res = hsql(*duck, "--csv", "-o", f"{destination}/", "-c", "select 1")
    assert res.exit_code == ExitCode.OK
    assert (destination / "results_1.csv").is_file()


def test_an_existing_directory_takes_files_whatever_it_is_called(
    hsql: Hsql, duck: list[str], tmp_path: Path
) -> None:
    destination = tmp_path / "exports.old"
    destination.mkdir()
    res = hsql(*duck, "--csv", "-o", str(destination), "-c", "select 1")
    assert res.exit_code == ExitCode.OK
    assert (destination / "results_1.csv").is_file()


@pytest.mark.parametrize(
    "args,written",
    [
        (("--info",), "info.json"),
        (("--spec",), "spec.json"),
        (("--config", "show"), "config-show.toml"),
        (("--config", "show", "--format", "json"), "config-show.json"),
        (("--config", "schema"), "config-schema.json"),
        (("--config", "list-profiles", "--csv"), "config-list-profiles.csv"),
    ],
)
def test_a_mode_names_its_document_in_a_directory(
    hsql: Hsql, tmp_path: Path, args: tuple[str, ...], written: str
) -> None:
    """A document has no position to be numbered by, so it takes its own name --
    and the extension of what it is actually written as."""
    destination = tmp_path / "exports"
    res = hsql(*args, "-o", str(destination))
    assert res.stdout == ""
    assert (destination / written).stat().st_size > 0
    assert written in res.stderr


def test_format_none_names_no_file(hsql: Hsql, duck: list[str], tmp_path: Path) -> None:
    """The rows are discarded, so there is no file to write and no folder to
    make -- but the run still reports what it did."""
    destination = tmp_path / "exports"
    res = hsql(
        *duck, "--format", "none", "-o", str(destination), "--stats", "-c", "select 1"
    )
    assert res.exit_code == ExitCode.OK
    assert not destination.exists()
    assert json.loads(res.stderr.splitlines()[-1])["rows"] == 1


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

    monkeypatch.setattr("harlequin.first_pass.load_profile", counting)
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
    from harlequin.first_pass import attach_adapter_options, command_spellings
    from harlequin.options import TextOption

    class _Adapter:
        ADAPTER_OPTIONS = [
            TextOption(name="collides", description="x", short_decls=["-c"]),
            TextOption(name="format", description="x"),
            TextOption(name="fine", description="x", short_decls=["-Z"]),
        ]

    cmd = build_cli(["--help"])
    reserved, taken = command_spellings(cmd)
    attach_adapter_options(
        cmd,
        cast("type[HarlequinAdapter]", _Adapter),
        reserved=reserved,
        taken=taken,
    )
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
    res = hsql("--config", "profiles")
    assert res.exit_code == ExitCode.USAGE
    for mode in CONFIG_MODES:
        assert mode in res.stderr


def test_config_modes_are_what_the_help_offers(hsql: Hsql) -> None:
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
    assert options["properties"]["no_init"]["type"] == "boolean"
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


# --- `--config init`, the mode that writes one -------------------------------


@pytest.fixture
def init_dirs(
    config_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """`config_dirs`, with the mock cwd as the process's working directory too.

    With no config file anywhere, `--config init` writes one beside the project
    it is configuring, and that fallback is `Path.cwd()` -- which patching the
    search path does not reach.
    """
    monkeypatch.chdir(config_dirs[0])
    return config_dirs


def written(path: Path) -> dict[str, Any]:
    """A config file as the loader reads it, to assert on what init wrote."""
    from harlequin.config import ConfigFile

    return ConfigFile(path).relevant_config


def test_config_init_writes_a_profile_where_there_was_no_config_file(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """The first config file a caller has, in the directory they are in.

    Not the home directory: a caller who has said nothing about where their
    config lives gets it beside the project they were configuring, which is
    also the file the next command discovers first.
    """
    cwd, home = init_dirs
    res = hsql("--config", "init", "-P", "prod", "-a", "sqlite", "./my.db")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert not (home / ".harlequin.toml").exists()
    assert written(cwd / ".harlequin.toml") == {
        "profiles": {"prod": {"adapter": "sqlite", "conn_str": ["./my.db"]}}
    }


def test_config_init_says_what_it_wrote_and_where(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """A mode that writes a file produces no data, so this is the whole answer."""
    cwd, _ = init_dirs
    res = hsql("--config", "init", "-P", "prod")
    assert res.exit_code == ExitCode.OK
    assert "prod" in res.stderr
    assert str(cwd / ".harlequin.toml") in res.stderr


def test_config_init_writes_what_was_typed_and_nothing_else(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """An option left at its default carries no intent, so it is not a key.

    The same rule `merge_profile_with_cli()` reads the other way round: a
    profile full of the command's own defaults would pin every one of them
    against the day one changes.
    """
    cwd, _ = init_dirs
    res = hsql("--config", "init", "-P", "prod", "--limit", "10")
    assert res.exit_code == ExitCode.OK
    profile = written(cwd / ".harlequin.toml")["profiles"]["prod"]
    assert profile == {"adapter": "duckdb", "limit": 10}


def test_config_init_writes_an_adapters_own_options(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """The half of a profile only the adapter declares.

    Which is why this is the one mode that imports an adapter to write a file:
    `--no-init` is not a flag hsql has until sqlite's options are on it.
    """
    cwd, _ = init_dirs
    res = hsql("--config", "init", "-P", "prod", "-a", "sqlite", "--no-init")
    assert res.exit_code == ExitCode.OK
    profile = written(cwd / ".harlequin.toml")["profiles"]["prod"]
    assert profile == {"adapter": "sqlite", "no_init": True}


def test_config_init_writes_a_shorthand_as_the_format_it_stands_for(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """`--csv` is a way of spelling `--format csv`, and a profile has the key."""
    cwd, _ = init_dirs
    res = hsql("--config", "init", "-P", "prod", "--csv")
    assert res.exit_code == ExitCode.OK
    profile = written(cwd / ".harlequin.toml")["profiles"]["prod"]
    assert profile == {"adapter": "duckdb", "format": "csv"}


def test_config_init_names_the_adapter_even_when_nothing_did(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """A profile that names no adapter is one the default could move under."""
    cwd, _ = init_dirs
    res = hsql("--config", "init", "-P", "prod")
    assert res.exit_code == ExitCode.OK
    assert written(cwd / ".harlequin.toml")["profiles"]["prod"]["adapter"] == "duckdb"


def test_config_init_keeps_everything_it_was_not_asked_to_write(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """The reason it writes through tomlkit rather than dumping a document.

    A caller's comments, key order and the profiles beside the one being
    written all survive, because a config file is a file a person wrote.
    """
    cwd, _ = init_dirs
    path = cwd / ".harlequin.toml"
    path.write_text(
        'default_profile = "dev"\n'
        "\n"
        "# the one I use every day\n"
        "[profiles.dev]\n"
        'adapter = "duckdb"\n'
        "# a big limit, because the laptop can take it\n"
        "limit = 500000\n"
    )
    res = hsql("--config", "init", "-P", "prod", "-a", "sqlite")
    assert res.exit_code == ExitCode.OK

    after = path.read_text()
    assert "# the one I use every day" in after
    assert "# a big limit, because the laptop can take it" in after
    assert written(path) == {
        "default_profile": "dev",
        "profiles": {
            "dev": {"adapter": "duckdb", "limit": 500000},
            "prod": {"adapter": "sqlite"},
        },
    }


def test_config_init_replaces_the_profile_it_names(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """A profile is written whole, and says so.

    The alternative -- merging into what is there -- leaves a caller unable to
    remove a key without editing the file by hand, and unable to tell from the
    command what the profile now says.
    """
    cwd, _ = init_dirs
    path = cwd / ".harlequin.toml"
    path.write_text('[profiles.prod]\nadapter = "sqlite"\nread_only = true\n')
    res = hsql("--config", "init", "-P", "prod", "-a", "sqlite")
    assert res.exit_code == ExitCode.OK
    assert "replaced" in res.stderr
    assert written(path)["profiles"]["prod"] == {"adapter": "sqlite"}


def test_config_init_writes_the_file_config_path_names(
    hsql: Hsql, init_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    """A path that is not there yet, which is the file this mode exists to make."""
    cwd, _ = init_dirs
    destination = tmp_path / "nested" / "harlequin.toml"
    res = hsql("--config", "init", "-P", "prod", "--config-path", str(destination))
    assert res.exit_code == ExitCode.OK
    assert written(destination)["profiles"]["prod"] == {"adapter": "duckdb"}
    assert not (cwd / ".harlequin.toml").exists()


def test_config_init_writes_the_nearest_config_file_that_exists(
    hsql: Hsql, two_config_files: tuple[Path, Path]
) -> None:
    """Where the wizard starts, for the same reason: it is the file that wins."""
    project, home = two_config_files
    before = home.read_text()
    res = hsql("--config", "init", "-P", "new", "-a", "sqlite")
    assert res.exit_code == ExitCode.OK
    assert "new" in written(project)["profiles"]
    assert home.read_text() == before


def test_config_init_reads_no_profile_at_all(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """The profile `-P` names is the one being written, not one to run under.

    So a name no file defines is the point rather than an error -- and a config
    that would refuse a run for a `default_profile` naming nothing still gets
    written to.
    """
    cwd, _ = init_dirs
    path = cwd / ".harlequin.toml"
    path.write_text('default_profile = "gone"\n')
    res = hsql("--config", "init", "-P", "prod", "-a", "sqlite")
    assert res.exit_code == ExitCode.OK
    assert written(path)["profiles"]["prod"] == {"adapter": "sqlite"}


def test_config_init_writes_a_profile_the_next_invocation_runs_under(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """The whole of what this mode is for, in two invocations.

    Nothing else asserts that what init writes is what the run path reads: the
    keys are hsql's own spellings on the way in and a profile's on the way out,
    and this is where those two agree.
    """
    res = hsql("--config", "init", "-P", "lite", "-a", "sqlite", ":memory:", "-tA")
    assert res.exit_code == ExitCode.OK

    res = hsql("-P", "lite", "-c", "select 42")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "42\n"


def test_config_init_needs_a_name_for_the_profile_it_writes(hsql: Hsql) -> None:
    res = hsql("--config", "init")
    assert res.exit_code == ExitCode.USAGE
    assert "-P" in res.stderr


def test_config_init_refuses_the_name_that_means_no_profile(hsql: Hsql) -> None:
    """`-P None` asks for Harlequin's defaults, so it names nothing to write."""
    res = hsql("--config", "init", "-P", "None")
    assert res.exit_code == ExitCode.USAGE
    assert "None" in res.stderr


def test_config_init_refuses_a_destination_that_is_not_toml(
    hsql: Hsql, tmp_path: Path
) -> None:
    destination = tmp_path / "harlequin.ini"
    res = hsql("--config", "init", "-P", "prod", "--config-path", str(destination))
    assert res.exit_code == ExitCode.USAGE
    assert not destination.exists()


def test_config_init_refuses_a_file_it_cannot_parse(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """Half-understanding a file is the one way this could destroy one."""
    cwd, _ = init_dirs
    path = cwd / ".harlequin.toml"
    path.write_text("[profiles.prod\nadapter = 'duckdb'\n")
    res = hsql("--config", "init", "-P", "other")
    assert res.exit_code == ExitCode.USAGE
    assert path.read_text() == "[profiles.prod\nadapter = 'duckdb'\n"


def test_config_init_refuses_a_file_whose_profiles_are_not_profiles(
    hsql: Hsql, init_dirs: tuple[Path, Path]
) -> None:
    """The write path reads a file raw, so this is the shape it cannot assume."""
    cwd, _ = init_dirs
    path = cwd / ".harlequin.toml"
    path.write_text("profiles = 3\n")
    res = hsql("--config", "init", "-P", "prod")
    assert res.exit_code == ExitCode.USAGE
    assert path.read_text() == "profiles = 3\n"


def test_config_init_does_not_run_sql(hsql: Hsql, init_dirs: tuple[Path, Path]) -> None:
    cwd, _ = init_dirs
    res = hsql("--config", "init", "-P", "prod", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert "does not run SQL" in res.stderr
    assert not (cwd / ".harlequin.toml").exists()


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


def test_spec_reports_every_option_in_one_vocabulary(hsql: Hsql) -> None:
    """Which is the point of the document: `float range` and `file` are click's
    own words for a number and a path, and a caller reading a spec should not
    have to learn click to know which is which."""
    from harlequin.hsql.modes.spec import TYPES

    spec = spec_of(hsql("--spec"))
    reported = {option["type"] for option in spec["options"]}
    reported |= {argument["type"] for argument in spec["arguments"]}
    reported |= {
        option["type"]
        for adapter in spec["adapters"].values()
        for option in adapter["options"]
    }
    assert reported <= set(TYPES.values())


def test_spec_reports_the_types_behind_seconds_and_a_config_path(
    hsql: Hsql,
) -> None:
    spec = spec_of(hsql("--spec"))
    assert option_named(spec, "timeout")["type"] == "number"
    assert option_named(spec, "config_path")["type"] == "path"


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

    `--no-init` is what a command line takes; `no_init` is what a profile
    writes, and what the adapter's constructor is handed. A caller that had
    only one of them would write the other wrong.
    """
    duckdb = spec_of(hsql("--spec"))["adapters"]["duckdb"]
    (no_init,) = [o for o in duckdb["options"] if o["name"] == "no_init"]
    assert no_init["decls"] == ["--no-init"]
    assert no_init["type"] == "boolean"
    assert no_init["is_flag"] is True
    assert no_init["default"] is False
    assert no_init["help"]


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


# --- `--catalog`, the mode that lists one level of the catalog ----------------

CATALOG_DDL = (
    "create schema analytics; "
    "create table analytics.orders "
    "(id bigint, customer_id bigint, total decimal(18,2)); "
    "create view analytics.order_summary as select 1 as n; "
    'create schema "empty schema"; '
    'create schema "my.schema"; '
    'create table "my.schema"."t" (n bigint); '
    "create schema \u5206\u6790"
)
"""Two levels below the database, a node with no children, and two labels a path
cannot spell without quoting one and measuring the other in terminal cells."""


def cells(stdout: str) -> list[list[str]]:
    """One `-tA` listing, as rows of cells."""
    return [row.split("|") for row in stdout.splitlines()]


def path_of(stdout: str, name: str) -> str:
    """The path cell of the row named `name`, which lists that item's children."""
    return next(row[0] for row in cells(stdout) if row[1] == name)


@pytest.fixture
def catalog_db(hsql: Hsql, tmp_path: Path) -> list[str]:
    """The arguments that reach a DuckDB file with a known catalog in it."""
    argv = ["-a", "duckdb", "--no-init", str(tmp_path / "cat.db")]
    res = hsql(*argv, "--format", "none", "-c", CATALOG_DDL)
    assert res.exit_code == ExitCode.OK
    return argv


def test_catalog_lists_the_top_level(hsql: Hsql, catalog_db: list[str]) -> None:
    res = hsql(*catalog_db, "--catalog", "-tA")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == 'cat|cat|"cat"|database|db\n'


def test_catalog_lists_one_level_below_path(hsql: Hsql, catalog_db: list[str]) -> None:
    res = hsql(*catalog_db, "--catalog", "--path", "cat", "-tA")
    assert res.exit_code == ExitCode.OK
    assert [row[1] for row in cells(res.stdout)] == [
        "analytics",
        "empty schema",
        "main",
        "my.schema",
        "\u5206\u6790",
    ]


def test_catalog_describes_a_relation_by_listing_it(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """There is no second mode for describing: a relation's children are its
    columns, with their types and their quoted identifiers."""
    res = hsql(*catalog_db, "--catalog", "--path", "cat.analytics.orders", "-tA")
    assert res.exit_code == ExitCode.OK
    assert cells(res.stdout) == [
        [
            "cat.analytics.orders.customer_id",
            "customer_id",
            '"customer_id"',
            "BIGINT",
            "##",
        ],
        ["cat.analytics.orders.id", "id", '"id"', "BIGINT", "##"],
        ["cat.analytics.orders.total", "total", '"total"', "DECIMAL(18,2)", "#.#"],
    ]


def test_every_level_reports_the_type_its_database_calls_it(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """`type` is the adapter's own vocabulary, not one core invented for it."""
    res = hsql(*catalog_db, "--catalog", "--path", "cat.analytics", "-tA")
    assert res.exit_code == ExitCode.OK
    assert [(row[1], row[3]) for row in cells(res.stdout)] == [
        ("order_summary", "VIEW"),
        ("orders", "BASE TABLE"),
    ]

    res = hsql(*catalog_db, "--catalog", "--path", "cat", "-tA")
    assert res.exit_code == ExitCode.OK
    assert {row[3] for row in cells(res.stdout)} == {"schema"}


def test_an_item_with_no_type_keeps_its_short_label(tmp_path: Path) -> None:
    """The degradation path, which no in-tree adapter takes: `type` is beside
    `type_label` rather than instead of it, so a listing still says what these
    things are."""
    from harlequin.adapter import HarlequinConnection
    from harlequin.catalog import Catalog, CatalogItem
    from harlequin.hsql.modes import catalog as catalog_mode
    from harlequin.layout import LayoutOptions
    from harlequin.navigate import CatalogPath

    class UntypedConnection:
        def get_catalog(self) -> Catalog:
            return Catalog(
                items=[
                    CatalogItem(
                        qualified_identifier='"t"',
                        query_name='"t"',
                        label="t",
                        type_label="t",
                    )
                ]
            )

    destination = tmp_path / "listing.csv"
    with destination.open("wb") as out:
        catalog_mode.report(
            out,
            connection=cast("HarlequinConnection", UntypedConnection()),
            path=CatalogPath(),
            format_name="csv",
            layout_options=LayoutOptions(),
            file_options={},
        )
    assert destination.read_text(encoding="utf-8").splitlines() == [
        "path,name,query_name,type,type_label",
        't,t,"""t""",,t',
    ]


@pytest.mark.parametrize(
    ("adapter", "levels", "type_names"),
    [
        ("duckdb", ["main", "t"], ["BIGINT", "VARCHAR"]),
        ("sqlite", ["t"], ["bigint", "varchar"]),
    ],
)
def test_type_answers_for_every_bundled_adapter(
    hsql: Hsql,
    tmp_path: Path,
    adapter: str,
    levels: list[str],
    type_names: list[str],
) -> None:
    """Both in-tree adapters populate it, out of introspection they already do.

    Each spells a type its own way, which is the point of carrying the
    database's own name for it rather than a normalized one.
    """
    argv = ["-a", adapter, "--no-init", str(tmp_path / f"{adapter}.db")]
    res = hsql(*argv, "--format", "none", "-c", "create table t (n bigint, s varchar)")
    assert res.exit_code == ExitCode.OK, res.stderr

    res = hsql(*argv, "--catalog", "-tA")
    assert res.exit_code == ExitCode.OK
    database_path = cells(res.stdout)[0][0]

    res = hsql(*argv, "--catalog", "--path", ".".join([database_path, *levels]), "-tA")
    assert res.exit_code == ExitCode.OK, res.stderr
    assert [(row[1], row[3]) for row in cells(res.stdout)] == [
        ("n", type_names[0]),
        ("s", type_names[1]),
    ]


def test_the_path_column_is_what_lists_that_items_children(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """Walking a catalog is copying a cell out of the last answer.

    A path an agent has to spell for itself is one it will spell wrong for a
    label with a dot in it, which is the case this walks through.
    """
    path = ""
    for name in ("cat", "my.schema", "t"):
        argv = ["--catalog", *(["--path", path] if path else [])]
        res = hsql(*catalog_db, *argv, "-tA")
        assert res.exit_code == ExitCode.OK, res.stderr
        path = path_of(res.stdout, name)
    assert path == 'cat."my.schema".t'

    res = hsql(*catalog_db, "--catalog", "--path", path, "-tA")
    assert res.exit_code == ExitCode.OK
    assert [row[1] for row in cells(res.stdout)] == ["n"]


def test_catalog_measures_a_label_in_terminal_cells(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """A listing is laid out like any other result set: two ideographs are four
    cells, where `len()` would call them two."""
    res = hsql(*catalog_db, "--catalog", "--path", "cat")
    assert res.exit_code == ExitCode.OK
    # the path column is as wide as `cat.empty schema` and the name column as
    # wide as `empty schema`; measured with len() these would be two cells short
    assert " cat.\u5206\u6790         | \u5206\u6790         |" in res.stdout


def test_catalog_query_name_is_the_adapters_own_quoting(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """The reason it is a column: the agent never guesses at a spelling."""
    res = hsql(*catalog_db, "--catalog", "--path", "cat.analytics", "-tA")
    assert res.exit_code == ExitCode.OK
    assert '"analytics"."orders"' in res.stdout


def test_a_trailing_wildcard_filters_one_level(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    res = hsql(*catalog_db, "--catalog", "--path", "cat.analytics.order_s*", "-tA")
    assert res.exit_code == ExitCode.OK
    assert [row[1] for row in cells(res.stdout)] == ["order_summary"]


def test_an_interior_wildcard_is_a_usage_error(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """It cannot be answered in one round trip, so it is refused rather than
    quietly walked."""
    res = hsql(*catalog_db, "--catalog", "--path", "cat.*.orders")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "wildcard" in res.stderr


def test_a_path_that_names_nothing_says_what_is_there(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    res = hsql(*catalog_db, "--catalog", "--path", "cat.analytic")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "Did you mean analytics?" in res.stderr


def test_a_node_with_no_children_is_zero_rows(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """Not an error: an empty schema is an answer, and the footer says so."""
    res = hsql(*catalog_db, "--catalog", "--path", 'cat."empty schema"')
    assert res.exit_code == ExitCode.OK
    assert res.stdout.endswith("(0 rows)\n")


def test_catalog_takes_every_format_a_result_set_does(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """A listing is rows, so it inherits the output layer rather than adding one."""
    res = hsql(*catalog_db, "--catalog", "--path", "cat.analytics", "--csv")
    assert res.exit_code == ExitCode.OK
    assert res.stdout.splitlines()[0] == "path,name,query_name,type,type_label"

    res = hsql(*catalog_db, "--catalog", "--path", "cat.analytics", "--json")
    assert res.exit_code == ExitCode.OK
    assert [row["name"] for row in json.loads(res.stdout)] == [
        "order_summary",
        "orders",
    ]


def test_catalog_goes_to_the_file_dash_o_names(
    hsql: Hsql, catalog_db: list[str], tmp_path: Path
) -> None:
    destination = tmp_path / "catalog.csv"
    res = hsql(*catalog_db, "--catalog", "-o", str(destination), "--csv")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert destination.read_text(encoding="utf-8").splitlines()[1].startswith("cat,cat")


def test_catalog_writes_nothing_under_format_none(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    res = hsql(*catalog_db, "--catalog", "--format", "none")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""


def test_display_rows_caps_a_listing(hsql: Hsql, catalog_db: list[str]) -> None:
    """A listing with four hundred rows in it is a display problem, and the
    layouts already solved it."""
    res = hsql(*catalog_db, "--catalog", "--path", "cat", "--display-rows", "2")
    assert res.exit_code == ExitCode.OK
    assert res.stdout.endswith("(2 of 5 rows)\n")


def test_display_rows_says_on_stderr_what_the_footer_cannot(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    res = hsql(*catalog_db, "--catalog", "--path", "cat", "--display-rows", "2", "-t")
    assert res.exit_code == ExitCode.OK
    assert len(res.stdout.splitlines()) == 2
    assert "printed 2 of 5 rows" in res.stderr


def test_limit_says_it_did_not_reach_the_listing(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """It is the hard fetch limit, and a listing is however many objects the
    adapter reported. Silence would read as a limit that was applied."""
    res = hsql(*catalog_db, "--catalog", "--path", "cat", "--limit", "1", "-tA")
    assert res.exit_code == ExitCode.OK
    assert len(res.stdout.splitlines()) == 5
    assert "--limit" in res.stderr and "no effect" in res.stderr


def test_limit_left_at_its_default_says_nothing(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    res = hsql(*catalog_db, "--catalog", "-tA")
    assert res.exit_code == ExitCode.OK
    assert res.stderr == ""


def test_catalog_does_not_run_sql(hsql: Hsql, catalog_db: list[str]) -> None:
    res = hsql(*catalog_db, "--catalog", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "does not run SQL" in res.stderr


@pytest.mark.parametrize("other", [["--info"], ["--spec"], ["--config", "show"]])
def test_catalog_beside_another_mode_is_a_usage_error(
    hsql: Hsql, other: list[str]
) -> None:
    res = hsql("--catalog", *other)
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "--catalog" in res.stderr


def test_path_without_catalog_is_a_usage_error(hsql: Hsql, duck: list[str]) -> None:
    """A flag that silently did nothing is the thing this command does not have."""
    res = hsql(*duck, "--path", "main", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "--path must be used with --catalog" in res.stderr


def test_catalog_carries_the_adapters_options(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """It connects, so unlike the other modes it takes what a connection takes.

    `--force-install-extensions` because it reaches the adapter and changes
    nothing on the way: with no `--extension` beside it there is nothing to
    install.
    """
    res = hsql(*catalog_db, "--catalog", "--force-install-extensions", "-tA")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == 'cat|cat|"cat"|database|db\n'


def test_catalog_reads_the_profile_a_run_would(
    hsql: Hsql, catalog_db: list[str], tmp_path: Path
) -> None:
    """It connects like a run, so it is configured like one."""
    config_file = tmp_path / "profile.toml"
    # json.dumps, not an f-string: TOML reads a backslash in a basic string as
    # an escape, so a Windows path would be a parse error rather than a path.
    config_file.write_text(
        f'[profiles.cat]\nadapter = "duckdb"\n'
        f"conn_str = [{json.dumps(catalog_db[-1])}]\n"
        "no_init = true\n"
    )
    res = hsql("--config-path", str(config_file), "-P", "cat", "--catalog", "-tA")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == 'cat|cat|"cat"|database|db\n'


def test_catalog_answers_for_every_bundled_adapter(
    hsql: Hsql, both_adapters: list[str]
) -> None:
    """Every adapter names its levels differently, and every one has a top."""
    res = hsql(*both_adapters, "--catalog", "-tA")
    assert res.exit_code == ExitCode.OK
    assert [row[4] for row in cells(res.stdout)] == ["db"]


def test_catalog_is_in_the_help(hsql: Hsql) -> None:
    res = hsql("--help")
    assert res.exit_code == ExitCode.OK
    assert "--catalog" in res.output
    assert "--path" in res.output
    assert f"{PROGRAM} --catalog --path" in res.output


# --- `--catalog-search`, which searches a catalog instead of walking it -----


def test_catalog_search_reaches_every_level_in_one_ask(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """The question a walk cannot answer: where the thing named that lives,
    whatever level it is on."""
    res = hsql(*catalog_db, "--catalog-search", "order", "-tA")
    assert res.exit_code == ExitCode.OK, res.stderr
    assert [(row[0], row[3]) for row in cells(res.stdout)] == [
        ("cat.analytics.order_summary", "VIEW"),
        ("cat.analytics.orders", "BASE TABLE"),
    ]


def test_catalog_search_matches_a_column_wherever_it_is(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    res = hsql(*catalog_db, "--catalog-search", "CUSTOMER_ID", "-tA")
    assert res.exit_code == ExitCode.OK, res.stderr
    assert cells(res.stdout) == [
        [
            "cat.analytics.orders.customer_id",
            "customer_id",
            '"customer_id"',
            "BIGINT",
            "##",
        ]
    ]


def test_catalog_search_matches_the_levels_above_a_relation_too(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """A caller searching a catalog does not know its shape, so zero rows has
    to mean nothing is named that -- not that the level was not looked at."""
    res = hsql(*catalog_db, "--catalog-search", "analytics", "-tA")
    assert res.exit_code == ExitCode.OK, res.stderr
    assert [(row[0], row[4]) for row in cells(res.stdout)] == [("cat.analytics", "sch")]


def test_a_found_path_is_a_path_catalog_walks(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """The two modes build their rows the same way, so a cell copied out of one
    is an argument to the other -- including for a label with a dot in it."""
    res = hsql(*catalog_db, "--catalog-search", "t", "-tA")
    assert res.exit_code == ExitCode.OK, res.stderr
    found = path_of(res.stdout, "t")
    assert found == 'cat."my.schema".t'

    res = hsql(*catalog_db, "--catalog", "--path", found, "-tA")
    assert res.exit_code == ExitCode.OK, res.stderr
    assert [row[1] for row in cells(res.stdout)] == ["n"]


def test_path_scopes_a_find(hsql: Hsql, catalog_db: list[str]) -> None:
    res = hsql(*catalog_db, "--catalog-search", "n", "--path", 'cat."my.schema"', "-tA")
    assert res.exit_code == ExitCode.OK, res.stderr
    assert [row[0] for row in cells(res.stdout)] == ['cat."my.schema".t.n']


def test_a_scope_that_names_nothing_says_what_is_there(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    res = hsql(*catalog_db, "--catalog-search", "orders", "--path", "cat.analytic")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "Did you mean analytics?" in res.stderr


def test_a_wildcard_scope_is_a_usage_error(hsql: Hsql, catalog_db: list[str]) -> None:
    """`--catalog-search` already matches on a term; a second filter over the
    same names is the same question asked twice."""
    res = hsql(*catalog_db, "--catalog-search", "orders", "--path", "cat.analytic*")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "wildcard" in res.stderr


def test_a_term_that_matches_nothing_is_zero_rows(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    res = hsql(*catalog_db, "--catalog-search", "nothing-is-called-this")
    assert res.exit_code == ExitCode.OK
    assert res.stdout.endswith("(0 rows)\n")


def test_a_blank_term_is_a_usage_error(hsql: Hsql, catalog_db: list[str]) -> None:
    """`--catalog-search "$NAME"` with nothing in NAME is a mistake, not a
    request for the whole catalog."""
    res = hsql(*catalog_db, "--catalog-search", "")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "--catalog-search needs a term" in res.stderr


def test_catalog_search_refuses_an_adapter_that_does_not_declare_it(
    hsql: Hsql, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused before it connects: an adapter that cannot search is one whose
    catalog would have to be walked, and a `--catalog-search` that quietly
    walked it is the round-trip cliff this mode exists to avoid."""
    from harlequin.adapter import HarlequinAdapter, HarlequinConnection

    class Unsearchable(HarlequinAdapter):
        ADAPTER_OPTIONS = None

        def __init__(self, conn_str: Sequence[str], **options: Any) -> None:
            pass

        def connect(self) -> HarlequinConnection:
            raise AssertionError("connected before checking the declaration")

    entry_point = MagicMock()
    entry_point.name = "unsearchable"
    entry_point.load.return_value = Unsearchable
    monkeypatch.setattr("harlequin.plugins.entry_points", lambda group: [entry_point])

    res = hsql("-a", "unsearchable", "--catalog-search", "orders")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "unsearchable" in res.stderr
    assert "--catalog" in res.stderr and "--info" in res.stderr


def test_catalog_search_takes_every_format_a_result_set_does(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    res = hsql(*catalog_db, "--catalog-search", "order", "--csv")
    assert res.exit_code == ExitCode.OK
    assert res.stdout.splitlines()[0] == "path,name,query_name,type,type_label"

    res = hsql(*catalog_db, "--catalog-search", "order", "--json")
    assert res.exit_code == ExitCode.OK
    assert [row["name"] for row in json.loads(res.stdout)] == [
        "order_summary",
        "orders",
    ]


def test_catalog_search_goes_to_the_file_dash_o_names(
    hsql: Hsql, catalog_db: list[str], tmp_path: Path
) -> None:
    destination = tmp_path / "found.csv"
    res = hsql(
        *catalog_db, "--catalog-search", "orders", "-o", str(destination), "--csv"
    )
    assert res.exit_code == ExitCode.OK
    assert res.stdout == ""
    assert (
        destination.read_text(encoding="utf-8")
        .splitlines()[1]
        .startswith("cat.analytics.orders,orders")
    )


def test_display_rows_caps_a_find(hsql: Hsql, catalog_db: list[str]) -> None:
    res = hsql(*catalog_db, "--catalog-search", "order", "--display-rows", "1")
    assert res.exit_code == ExitCode.OK
    assert res.stdout.endswith("(1 of 2 rows)\n")


def test_limit_says_it_did_not_reach_the_find(
    hsql: Hsql, catalog_db: list[str]
) -> None:
    """It is the hard fetch limit, and a search is however many objects the
    adapter reported. Silence would read as a limit that was applied."""
    res = hsql(*catalog_db, "--catalog-search", "order", "--limit", "1", "-tA")
    assert res.exit_code == ExitCode.OK
    assert len(res.stdout.splitlines()) == 2
    assert "--limit" in res.stderr and "no effect" in res.stderr


def test_catalog_search_does_not_run_sql(hsql: Hsql, catalog_db: list[str]) -> None:
    res = hsql(*catalog_db, "--catalog-search", "orders", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "does not run SQL" in res.stderr


@pytest.mark.parametrize(
    "other", [["--catalog"], ["--info"], ["--spec"], ["--config", "show"]]
)
def test_catalog_search_beside_another_mode_is_a_usage_error(
    hsql: Hsql, other: list[str]
) -> None:
    res = hsql("--catalog-search", "orders", *other)
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "--catalog-search" in res.stderr


@pytest.mark.parametrize("adapter", ["duckdb", "sqlite"])
def test_catalog_search_answers_for_every_bundled_adapter(
    hsql: Hsql, tmp_path: Path, adapter: str
) -> None:
    """Both in-tree adapters implement the search, rather than declaring the
    capability and leaving it to the ecosystem. Each spells the path to what it
    found its own way, and each spells one `--catalog --path` walks."""
    argv = ["-a", adapter, "--no-init", str(tmp_path / f"{adapter}.db")]
    res = hsql(
        *argv, "--format", "none", "-c", "create table orders (customer_id bigint)"
    )
    assert res.exit_code == ExitCode.OK, res.stderr

    res = hsql(*argv, "--catalog-search", "customer_id", "-tA")
    assert res.exit_code == ExitCode.OK, res.stderr
    assert [row[1] for row in cells(res.stdout)] == ["customer_id"]
    assert path_of(res.stdout, "customer_id").endswith("orders.customer_id")


def test_every_bundled_adapter_declares_catalog_search(hsql: Hsql) -> None:
    """`--info` is where a caller learns which adapters can search."""
    res = hsql("--info")
    assert res.exit_code == ExitCode.OK
    adapters = json.loads(res.stdout)["adapters"]
    assert all(
        adapters[name]["capabilities"]["implements_catalog_search"]
        for name in ("duckdb", "sqlite")
    )


def test_catalog_search_is_in_the_help(hsql: Hsql) -> None:
    res = hsql("--help")
    assert res.exit_code == ExitCode.OK
    assert "--catalog-search" in res.output
    assert f"{PROGRAM} --catalog-search" in res.output


# --- `--read-only`, and the capability it needs -------------------------------


@pytest.fixture
def declares_nothing(monkeypatch: pytest.MonkeyPatch) -> str:
    """The name of an installed adapter that declares no capability at all.

    Which is every adapter the day before it adds a declaration, so this is
    what a refusal has to be right about -- and `connect()` raising is what
    proves the refusal came first.
    """
    from harlequin.adapter import HarlequinAdapter, HarlequinConnection

    class DeclaresNothing(HarlequinAdapter):
        ADAPTER_OPTIONS = None

        def __init__(self, conn_str: Sequence[str], **options: Any) -> None:
            pass

        def connect(self) -> HarlequinConnection:
            raise AssertionError("connected before checking the declaration")

    entry_point = MagicMock()
    entry_point.name = "undeclared"
    entry_point.load.return_value = DeclaresNothing
    monkeypatch.setattr("harlequin.plugins.entry_points", lambda group: [entry_point])
    return "undeclared"


def test_read_only_refuses_an_adapter_that_does_not_declare_it(
    hsql: Hsql, declares_nothing: str
) -> None:
    """A flag that no-ops is worse than one that is absent.

    An adapter drops an option it does not recognize, so a run that believed it
    was read-only would write. Refused before it connects, which is the whole
    of what the flag promises.
    """
    res = hsql("-a", declares_nothing, "--read-only", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert declares_nothing in res.stderr
    assert "--read-only" in res.stderr and "--info" in res.stderr


def test_read_only_from_a_profile_is_refused_the_same_way(
    hsql: Hsql, declares_nothing: str, tmp_path: Path
) -> None:
    """The spelling a human is likelier to have used, and to trust.

    `read_only = true` in a profile is a promise made once and relied on by
    every invocation that loads it, so it is refused where the flag is.
    """
    path = tmp_path / ".harlequin.toml"
    path.write_text("[profiles.prod]\nread_only = true\n")
    res = hsql(
        "--config-path",
        str(path),
        "-P",
        "prod",
        "-a",
        declares_nothing,
        "-c",
        "select 1",
    )
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert declares_nothing in res.stderr
    assert "profile" in res.stderr


def test_read_only_is_refused_ahead_of_the_mode_that_would_connect(
    hsql: Hsql, declares_nothing: str
) -> None:
    """`--catalog` connects too, so it meets the same refusal."""
    res = hsql("-a", declares_nothing, "--read-only", "--catalog")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert declares_nothing in res.stderr


def test_read_only_is_refused_before_it_is_written_into_a_profile(
    hsql: Hsql, declares_nothing: str, init_dirs: tuple[Path, Path]
) -> None:
    """`--config init` imports the adapter to write the options it declares, so
    it can refuse a profile that names one and a read-only it cannot honor --
    which is a profile no run would start under."""
    cwd, _ = init_dirs
    res = hsql("--config", "init", "-P", "prod", "-a", declares_nothing, "--read-only")
    assert res.exit_code == ExitCode.USAGE
    assert declares_nothing in res.stderr
    assert not (cwd / ".harlequin.toml").exists()


def test_config_validate_reports_a_read_only_the_adapter_cannot_do(
    hsql: Hsql, declares_nothing: str, config_dirs: tuple[Path, Path]
) -> None:
    """`--config validate` imports an adapter per profile already, so it can
    say that a profile pairs one with a read-only it cannot honor."""
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        f"[profiles.prod]\nadapter = '{declares_nothing}'\nread_only = true\n"
    )

    # `-a` only to satisfy the choice of installed adapters; the mode reads the
    # adapter each profile names
    res = hsql("--config", "validate", "-a", declares_nothing, "-tA")
    assert res.exit_code == ExitCode.USAGE
    file, key, problem, _ = res.stdout.strip().split("|")
    assert file == str(cwd / ".harlequin.toml")
    assert key == "profiles.prod.read_only"
    assert declares_nothing in problem


@pytest.mark.parametrize(
    "mode", [["--info"], ["--spec"], ["--config", "show"], ["--config", "validate"]]
)
def test_read_only_does_not_refuse_a_mode_that_reads_no_database(
    hsql: Hsql, declares_nothing: str, mode: list[str], config_dirs: tuple[Path, Path]
) -> None:
    """None of them can write whatever they are told -- and two of them are
    where a caller finds out which adapter they are on and where its read-only
    came from, which is no use if the flag refuses them."""
    res = hsql(*mode, "-a", declares_nothing, "--read-only")
    assert res.exit_code == ExitCode.OK, res.stderr


def test_info_reports_an_adapter_that_cannot_be_read_only(
    hsql: Hsql, declares_nothing: str
) -> None:
    """`--info` is where the refusal points, so it answers rather than joining
    in."""
    res = hsql("--info", "-a", declares_nothing, "--read-only")
    assert res.exit_code == ExitCode.OK
    capabilities = info_of(res)["adapters"][declares_nothing]["capabilities"]
    assert capabilities["implements_read_only"] is False


def test_every_bundled_adapter_declares_read_only(hsql: Hsql) -> None:
    """`--info` is where a caller learns which adapters can be read-only."""
    adapters = info_of(hsql("--info"))["adapters"]
    assert all(
        adapters[name]["capabilities"]["implements_read_only"]
        for name in ("duckdb", "sqlite")
    )


def one_row_db(adapter: str, path: Path) -> list[str]:
    """A database with a table in it, written by the driver itself.

    Built outside hsql because a connection this process still holds is one
    duckdb will not reopen under a different configuration -- and read-only is
    a different configuration.
    """
    if adapter == "duckdb":
        import duckdb

        duck = duckdb.connect(str(path))
        duck.execute("create table t as select 1 as n")
        duck.close()
    else:
        import sqlite3

        lite = sqlite3.connect(str(path))
        lite.execute("create table t as select 1 as n")
        lite.commit()
        lite.close()
    return ["-a", adapter, "--no-init", str(path)]


@pytest.mark.parametrize("adapter", ["duckdb", "sqlite"])
def test_read_only_stops_a_write_on_every_bundled_adapter(
    hsql: Hsql, tmp_path: Path, adapter: str
) -> None:
    """The flag's whole job, end to end: reads answer, writes fail."""
    argv = one_row_db(adapter, tmp_path / f"{adapter}.db")

    res = hsql(*argv, "--read-only", "-c", "select n from t", "-tA")
    assert res.exit_code == ExitCode.OK, res.stderr
    assert res.stdout == "1\n"

    res = hsql(*argv, "--read-only", "-c", "insert into t values (2)")
    assert res.exit_code == ExitCode.QUERY
    assert res.stdout == ""
    assert res.stderr


@pytest.mark.parametrize("spelling", ["-r", "--read-only"])
def test_read_only_means_the_same_thing_however_it_is_spelled(
    hsql: Hsql, tmp_path: Path, spelling: str
) -> None:
    argv = one_row_db("sqlite", tmp_path / "one.db")

    res = hsql(*argv, spelling, "-c", "insert into t values (2)")
    assert res.exit_code == ExitCode.QUERY
    assert res.stdout == ""


def test_the_single_dash_long_spelling_is_not_one(hsql: Hsql, tmp_path: Path) -> None:
    """`-readonly` is two spellings' worth of nothing: `-r` and `--read-only`
    are what a caller has, on both commands and for every adapter."""
    argv = one_row_db("sqlite", tmp_path / "one.db")

    res = hsql(*argv, "-readonly", "-c", "insert into t values (2)")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""


def test_read_only_is_in_the_help(hsql: Hsql) -> None:
    """On the adapter-agnostic surface, because the refusal is too."""
    res = hsql("--help")
    assert res.exit_code == ExitCode.OK
    assert "--read-only" in res.output


# --- secrets, and the promise that none of them is printed -------------------

SECRET = "hunter2-and-then-some"
"""One value, in a profile and in a connection string, asserted for negatively.

Long enough that finding it in the output is unambiguous, and distinctive
enough that a substring search over every byte written means what it says.
"""


@pytest.fixture
def secret_config(config_dirs: tuple[Path, Path]) -> Path:
    """A profile with a secret in an option *and* in a connection string.

    Both halves, because they are hidden by two different mechanisms:
    `md_token` is masked because the DuckDB adapter declares it secret, and the
    token in the connection string because `redact_conn_str` reads the DSN.
    """
    cwd, _ = config_dirs
    path = cwd / ".harlequin.toml"
    path.write_text(
        'default_profile = "md"\n'
        "[profiles.md]\n"
        'adapter = "duckdb"\n'
        f'conn_str = [ "md:my_db?motherduck_token={SECRET}" ]\n'
        f'md_token = "{SECRET}"\n'
        "limit = 500\n"
    )
    return path


def test_info_prints_no_secret(hsql: Hsql, secret_config: Path) -> None:
    """The document a caller pastes into an issue."""
    res = hsql("--info")
    assert res.exit_code == ExitCode.OK
    assert SECRET not in res.output
    options = json.loads(res.stdout)["profile"]["options"]
    assert options["md_token"] == "********"
    assert options["conn_str"] == ["md:my_db?motherduck_token=********"]
    # and still the document it was: what a reader needs is all still there
    assert options["limit"] == 500
    assert options["adapter"] == "duckdb"


def test_config_show_prints_no_secret(hsql: Hsql, secret_config: Path) -> None:
    """Masked without importing an adapter to ask, which is why the fallback to
    the key's name exists."""
    res = hsql("--config", "show")
    assert res.exit_code == ExitCode.OK
    assert SECRET not in res.output
    assert 'md_token = "********"' in res.stdout
    assert "motherduck_token=********" in res.stdout
    # the provenance is the whole point of the mode, and survives
    assert f"# from {secret_config}" in res.stdout


def test_config_show_masks_a_secret_the_key_name_does_not_give_away(
    hsql: Hsql, config_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason this mode imports the adapters its profiles name.

    A key called `hush` is not one core could have guessed at, and guessing is
    what the fallback does. Only the adapter knows, so the mode asks it.
    """
    from harlequin.options import TextOption

    monkeypatch.setattr(
        "harlequin_duckdb.DuckDbAdapter.ADAPTER_OPTIONS",
        [TextOption(name="hush", description="x", secret=True)],
    )
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        f'[profiles.one]\nadapter = "duckdb"\nhush = "{SECRET}"\n'
    )
    res = hsql("--config", "show")
    assert res.exit_code == ExitCode.OK
    assert SECRET not in res.output
    assert 'hush = "********"' in res.stdout


def test_config_show_asks_the_default_adapter_for_a_profile_that_names_none(
    hsql: Hsql, config_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile with no `adapter` connects with the default, so that is the
    one whose declarations decide what it is hiding."""
    from harlequin.options import TextOption

    monkeypatch.setattr(
        "harlequin_duckdb.DuckDbAdapter.ADAPTER_OPTIONS",
        [TextOption(name="hush", description="x", secret=True)],
    )
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(f'[profiles.one]\nhush = "{SECRET}"\n')
    res = hsql("--config", "show")
    assert res.exit_code == ExitCode.OK
    assert SECRET not in res.output
    assert 'hush = "********"' in res.stdout


def test_config_show_says_when_it_masked_by_name_alone(
    hsql: Hsql, config_dirs: tuple[Path, Path]
) -> None:
    """The one path where a value this would have masked might print, so it is
    said out loud rather than left for a reader to notice."""
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        f'[profiles.one]\nadapter = "nonesuch"\npassword = "{SECRET}"\n'
    )
    res = hsql("--config", "show")
    assert res.exit_code == ExitCode.OK
    assert SECRET not in res.output
    assert 'password = "********"' in res.stdout
    assert "could not import nonesuch" in res.stderr


def test_config_show_json_prints_no_secret(hsql: Hsql, secret_config: Path) -> None:
    res = hsql("--config", "show", "--json")
    assert res.exit_code == ExitCode.OK
    assert SECRET not in res.output
    profile = json.loads(res.stdout)["profiles"]["md"]["value"]
    assert profile["md_token"] == "********"


def test_config_list_profiles_prints_no_secret(hsql: Hsql, secret_config: Path) -> None:
    """It prints names rather than values, and this is what says so."""
    res = hsql("--config", "list-profiles")
    assert res.exit_code == ExitCode.OK
    assert SECRET not in res.output
    assert "md" in res.stdout


def test_config_validate_prints_no_secret(hsql: Hsql, secret_config: Path) -> None:
    res = hsql("--config", "validate")
    assert SECRET not in res.output


def test_spec_prints_no_secret_and_says_which_options_are(
    hsql: Hsql, secret_config: Path
) -> None:
    """`--spec` publishes no values, and publishes which options hold one.

    That key is what teaches an agent not to construct `hsql --md_token
    hunter2`, where `ps` and a shell history can read it.
    """
    res = hsql("--spec", "-a", "duckdb")
    assert res.exit_code == ExitCode.OK
    assert SECRET not in res.output
    options = {
        entry["name"]: entry
        for entry in json.loads(res.stdout)["adapters"]["duckdb"]["options"]
    }
    assert options["md_token"]["secret"] is True
    assert options["no_init"]["secret"] is False
    # both halves of the document answer with the same keys
    assert all("secret" in entry for entry in json.loads(res.stdout)["options"])


def test_spec_masks_a_default_an_adapter_shipped_for_a_secret(
    hsql: Hsql, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An adapter that ships a default for a secret has shipped the secret, and
    this document would otherwise write it down for every installation."""
    from harlequin.options import TextOption

    monkeypatch.setattr(
        "harlequin_duckdb.DuckDbAdapter.ADAPTER_OPTIONS",
        [TextOption(name="md_token", description="x", default=SECRET, secret=True)],
    )
    res = hsql("--spec", "-a", "duckdb")
    assert res.exit_code == ExitCode.OK
    assert SECRET not in res.output
    (option,) = json.loads(res.stdout)["adapters"]["duckdb"]["options"]
    assert option["default"] == "********"


def test_an_error_that_echoes_the_connection_string_prints_no_secret(
    hsql: Hsql, secret_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backstop, and the case the option layer cannot reach: a driver that
    quotes back the DSN it was handed."""

    def exploding_connect(self: Any) -> Any:
        raise HarlequinConnectionError(f"could not connect to {self.conn_str[0]}")

    monkeypatch.setattr(
        "harlequin_duckdb.DuckDbAdapter.connect", exploding_connect, raising=True
    )
    res = hsql("-c", "select 1")
    assert res.exit_code == ExitCode.CONNECTION
    assert SECRET not in res.output
    assert "could not connect to md:my_db?motherduck_token=********" in res.stderr


class _RefusingConnection:
    """A connection that refuses every statement, quoting the DSN as it does.

    Which is the shape of the failure that matters here: the message comes from
    the driver, so nothing in the option layer has had a chance to shape it.
    """

    def execute(self, query: str) -> Any:
        raise HarlequinQueryError(f"rejected: md:my_db?motherduck_token={SECRET}")


def test_stats_prints_no_secret(
    hsql: Hsql, secret_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--stats` carries the failure's message, so it carries this promise too.

    It is a second channel rather than the same one twice: the error goes to
    stderr as prose, and this goes to stderr as JSON a script parses.
    """
    monkeypatch.setattr(
        "harlequin_duckdb.DuckDbAdapter.connect", lambda self: _RefusingConnection()
    )
    res = hsql("--stats", "-c", "select 1")
    assert res.exit_code == ExitCode.QUERY
    assert SECRET not in res.output
    stats = json.loads(res.stderr.strip().splitlines()[-1])
    assert stats["status"] == "error"
    assert "motherduck_token=********" in stats["error"]


def test_a_secret_typed_on_the_command_line_is_still_hidden_in_errors(
    hsql: Hsql, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Typing one there is the thing `--spec` teaches a caller not to do, and
    it is not a reason to print it back."""
    monkeypatch.chdir(tmp_path)

    def exploding_connect(self: Any) -> Any:
        raise HarlequinConnectionError(f"token {self.md_token} rejected")

    monkeypatch.setattr("harlequin_duckdb.DuckDbAdapter.connect", exploding_connect)
    res = hsql(
        "-a", "duckdb", "--no-init", ":memory:", "--md_token", SECRET, "-c", "select 1"
    )
    assert res.exit_code == ExitCode.CONNECTION
    assert SECRET not in res.output
    assert "token ******** rejected" in res.stderr


def test_nothing_is_hidden_from_a_run_with_no_secrets(
    hsql: Hsql, duck: list[str]
) -> None:
    """The other half: a message with nothing to hide is printed as it is."""
    res = hsql(*duck, "-c", "select nope")
    assert res.exit_code == ExitCode.QUERY
    assert "********" not in res.output
    assert "nope" in res.stderr


def test_the_secret_still_reaches_the_adapter(
    hsql: Hsql, secret_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redaction is for output. A run that connected with asterisks would be a
    worse bug than the one this is fixing."""
    seen: dict[str, Any] = {}

    def recording_connect(self: Any) -> Any:
        seen["md_token"] = self.md_token
        seen["conn_str"] = self.conn_str
        raise HarlequinConnectionError("stop here")

    monkeypatch.setattr("harlequin_duckdb.DuckDbAdapter.connect", recording_connect)
    hsql("-c", "select 1")
    assert seen["md_token"] == SECRET
    assert list(seen["conn_str"]) == [f"md:my_db?motherduck_token={SECRET}"]


# --- `${VAR}`, the environment a config file reads ----------------------------


def test_a_profile_reads_a_variable_from_the_environment(
    hsql: Hsql, config_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: a config file that names a variable, and a query that runs.

    Which is what the feature is for -- a profile a team shares, and a
    credential each of them keeps in their own environment.
    """
    monkeypatch.setenv("HARLEQUIN_TEST_DB", ":memory:")
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        'default_profile = "prod"\n'
        "[profiles.prod]\n"
        'adapter = "duckdb"\n'
        'conn_str = ["${HARLEQUIN_TEST_DB}"]\n'
        "no_init = true\n"
    )
    res = hsql("-tA", "-c", "select 42")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "42\n"


def test_an_unset_variable_is_a_usage_error_that_names_it(
    hsql: Hsql, config_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 2 before the connection, rather than an empty string three layers in."""
    monkeypatch.delenv("HARLEQUIN_TEST_TOKEN", raising=False)
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        'default_profile = "prod"\n'
        "[profiles.prod]\n"
        'adapter = "duckdb"\n'
        'md_token = "${HARLEQUIN_TEST_TOKEN}"\n'
    )
    res = hsql("-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "HARLEQUIN_TEST_TOKEN" in res.stderr
    assert str(cwd / ".harlequin.toml") in res.stderr


def test_info_reports_an_unset_variable_rather_than_refusing(
    hsql: Hsql, config_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller whose config is broken is one of the likeliest to run `--info`,
    so the variable it could not read is part of the answer."""
    monkeypatch.delenv("HARLEQUIN_TEST_TOKEN", raising=False)
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        'default_profile = "prod"\n'
        "[profiles.prod]\n"
        'adapter = "duckdb"\n'
        'md_token = "${HARLEQUIN_TEST_TOKEN}"\n'
    )
    res = hsql("--info")
    assert res.exit_code == ExitCode.OK
    profile = json.loads(res.stdout)["profile"]
    assert profile["options"] is None
    assert "HARLEQUIN_TEST_TOKEN" in profile["error"]


def test_config_validate_reports_an_unset_variable(
    hsql: Hsql, config_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mode that reads every file reports this like any other problem."""
    monkeypatch.delenv("HARLEQUIN_TEST_TOKEN", raising=False)
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        '[profiles.prod]\nadapter = "duckdb"\nmd_token = "${HARLEQUIN_TEST_TOKEN}"\n'
    )
    res = hsql("--config", "validate", "-tA")
    assert res.exit_code == ExitCode.USAGE
    file, key, problem, _ = res.stdout.strip().split("|")
    assert file == str(cwd / ".harlequin.toml")
    assert key == "profiles.prod.md_token"
    assert "HARLEQUIN_TEST_TOKEN" in problem


def test_config_show_prints_the_variable_rather_than_what_it_resolves_to(
    hsql: Hsql, config_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mode reports on config files, so it reports what they say.

    Which is also the answer a reader wants: `${HARLEQUIN_TEST_TOKEN}` says
    where the value comes from, where the value itself would not -- and an
    unset variable is not a reason to refuse a report about the file naming it.
    """
    monkeypatch.delenv("HARLEQUIN_TEST_TOKEN", raising=False)
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        '[profiles.md]\nadapter = "duckdb"\nconn_str = ["${HARLEQUIN_TEST_TOKEN}"]\n'
    )
    res = hsql("--config", "show")
    assert res.exit_code == ExitCode.OK
    assert 'conn_str = ["${HARLEQUIN_TEST_TOKEN}"]' in res.stdout


def test_info_masks_a_secret_read_from_the_environment(
    hsql: Hsql, config_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interpolation resolves a value; it does not make one printable.

    Both mechanisms, over a value that was never written in the file: the
    option DuckDB declares secret, and the credential inside the DSN.
    """
    monkeypatch.setenv("HARLEQUIN_TEST_TOKEN", SECRET)
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        'default_profile = "md"\n'
        "[profiles.md]\n"
        'adapter = "duckdb"\n'
        'conn_str = ["md:my_db?motherduck_token=${HARLEQUIN_TEST_TOKEN}"]\n'
        'md_token = "${HARLEQUIN_TEST_TOKEN}"\n'
    )
    res = hsql("--info")
    assert res.exit_code == ExitCode.OK
    assert SECRET not in res.output
    options = json.loads(res.stdout)["profile"]["options"]
    assert options["md_token"] == "********"
    assert options["conn_str"] == ["md:my_db?motherduck_token=********"]


def test_config_init_writes_the_variable_rather_than_what_it_resolves_to(
    hsql: Hsql, init_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing is the other side of reading, and it writes what it was given.

    `--config init` is how a caller puts `${MD_TOKEN}` in a config file from a
    script, so what lands in the file has to be the six characters they typed.
    """
    monkeypatch.setenv("HARLEQUIN_TEST_TOKEN", SECRET)
    cwd, _ = init_dirs
    res = hsql(
        "--config",
        "init",
        "-P",
        "md",
        "-a",
        "duckdb",
        "--md_token",
        "${HARLEQUIN_TEST_TOKEN}",
        "md:my_db",
    )
    assert res.exit_code == ExitCode.OK
    assert SECRET not in (cwd / ".harlequin.toml").read_text()
    assert written(cwd / ".harlequin.toml")["profiles"]["md"]["md_token"] == (
        "${HARLEQUIN_TEST_TOKEN}"
    )


def test_a_secret_read_from_the_environment_is_hidden_in_an_error(
    hsql: Hsql, config_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backstop reaches a value that was never in a config file either."""
    monkeypatch.setenv("HARLEQUIN_TEST_TOKEN", SECRET)
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        'default_profile = "md"\n'
        "[profiles.md]\n"
        'adapter = "duckdb"\n'
        'md_token = "${HARLEQUIN_TEST_TOKEN}"\n'
    )

    def exploding_connect(self: Any) -> Any:
        raise HarlequinConnectionError(f"token {self.md_token} rejected")

    monkeypatch.setattr("harlequin_duckdb.DuckDbAdapter.connect", exploding_connect)
    res = hsql("-c", "select 1")
    assert res.exit_code == ExitCode.CONNECTION
    assert SECRET not in res.output
    assert "token ******** rejected" in res.stderr


# --- `--timeout`, and the cancellation it has to attribute --------------------


class _StuckCursor:
    """A cursor whose fetch blocks until the connection is cancelled.

    What a cancelled DuckDB cursor does when it comes back: swallows the
    interrupt and returns no rows, which is also what a query that matched
    nothing returns. Nothing downstream can tell the two apart, which is why
    hsql attributes the timeout itself.
    """

    def __init__(self, released: threading.Event) -> None:
        self.released = released

    def columns(self) -> list[tuple[str, str]]:
        return [("n", "##")]

    def set_limit(self, limit: int) -> "_StuckCursor":
        return self

    def fetchall(self) -> None:
        self.released.wait(timeout=30)
        return None


class _StuckConnection:
    """A connection whose every query blocks until it is cancelled."""

    def __init__(self, *, stoppable: bool = True) -> None:
        self.stoppable = stoppable
        self.released = threading.Event()
        self.cancelled = False

    def execute(self, query: str) -> _StuckCursor:
        return _StuckCursor(self.released)

    def get_catalog(self) -> Any:
        self.released.wait(timeout=30)
        from harlequin.catalog import Catalog

        return Catalog(items=[])

    def cancel(self) -> None:
        self.cancelled = True
        if self.stoppable:
            self.released.set()


@pytest.fixture
def stuck_adapter(monkeypatch: pytest.MonkeyPatch) -> list[_StuckConnection]:
    """An installed adapter whose queries block until they are cancelled.

    A fake rather than a slow query: the deadline, the cancel and the
    attribution are what these tests are about, and a real query slow enough to
    time out is one every machine has a different opinion about.

    Yields the list the connections it opened land in, so a test can ask whether
    the cancel it promised actually happened.
    """
    from harlequin.adapter import HarlequinAdapter

    opened: list[_StuckConnection] = []

    class StuckAdapter(HarlequinAdapter):
        ADAPTER_OPTIONS = None
        IMPLEMENTS_CANCEL = True

        def __init__(self, conn_str: Sequence[str], **options: Any) -> None:
            pass

        def connect(self) -> Any:
            opened.append(_StuckConnection())
            return opened[-1]

    entry_point = MagicMock()
    entry_point.name = "stuck"
    entry_point.load.return_value = StuckAdapter
    monkeypatch.setattr("harlequin.plugins.entry_points", lambda group: [entry_point])
    return opened


def test_timeout_cancels_the_run_and_attributes_it(
    hsql: Hsql, stuck_adapter: list[_StuckConnection]
) -> None:
    """The whole flag, on the run it exists for.

    Nothing on stdout is the half a naive deadline gets wrong: the result set a
    cancel produces is empty and error-free, and printing it would report "your
    query returned nothing" for a query that was killed.
    """
    res = hsql("-a", "stuck", "--timeout", "0.1", "-c", "select 1")
    assert res.exit_code == ExitCode.TIMEOUT
    assert res.stdout == ""
    assert "timed out after 0.1s" in res.stderr
    assert stuck_adapter[0].cancelled


def test_timeout_stops_the_statements_after_the_one_it_cancelled(
    hsql: Hsql, stuck_adapter: list[_StuckConnection]
) -> None:
    """A script is stopped, not just the statement that ran too long."""
    res = hsql("-a", "stuck", "--timeout", "0.1", "-c", "select 1; select 2; select 3")
    assert res.exit_code == ExitCode.TIMEOUT
    assert res.stdout == ""
    # the one it was inside when the clock ran out, and none after it
    assert res.stderr.count("timed out") == 1


def test_timeout_is_what_stats_reports(
    hsql: Hsql, stuck_adapter: list[_StuckConnection]
) -> None:
    """`--stats` is the machine channel, so it says what stderr said."""
    res = hsql("-a", "stuck", "--timeout", "0.1", "--stats", "-c", "select 1")
    assert res.exit_code == ExitCode.TIMEOUT
    stats = json.loads(res.stderr.strip().splitlines()[-1])
    assert stats["status"] == "error"
    assert stats["error"] == "timed out after 0.1s"


def test_timeout_bounds_a_catalog_listing_too(
    hsql: Hsql, stuck_adapter: list[_StuckConnection]
) -> None:
    """`--catalog` connects and waits on the database like a run does, and a
    safety flag that silently did nothing there would be the worse half of the
    choice."""
    res = hsql("-a", "stuck", "--timeout", "0.1", "--catalog")
    assert res.exit_code == ExitCode.TIMEOUT
    assert "timed out after 0.1s" in res.stderr
    assert stuck_adapter[0].cancelled


def test_a_run_inside_the_deadline_is_untouched(hsql: Hsql, duck: list[str]) -> None:
    """The clock is the only thing the flag adds: same bytes, same code."""
    res = hsql(*duck, "--timeout", "60", "-c", "select 1 as a")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == " a\n---\n 1\n(1 row)\n"
    assert res.stderr == ""


def test_timeout_refuses_an_adapter_that_cannot_cancel(
    hsql: Hsql, declares_nothing: str
) -> None:
    """There is no way to stop the work, so a deadline could only lie about
    having stopped it -- which is worse than not having the flag."""
    res = hsql("-a", declares_nothing, "--timeout", "30", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert declares_nothing in res.stderr
    assert "--timeout" in res.stderr and "--info" in res.stderr


def test_timeout_from_a_profile_is_refused_the_same_way(
    hsql: Hsql, declares_nothing: str, tmp_path: Path
) -> None:
    path = tmp_path / ".harlequin.toml"
    path.write_text("[profiles.prod]\ntimeout = 30\n")
    res = hsql(
        "--config-path",
        str(path),
        "-P",
        "prod",
        "-a",
        declares_nothing,
        "-c",
        "select 1",
    )
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert declares_nothing in res.stderr
    assert "profile" in res.stderr


def test_timeout_is_refused_ahead_of_the_mode_that_would_connect(
    hsql: Hsql, declares_nothing: str
) -> None:
    res = hsql("-a", declares_nothing, "--timeout", "30", "--catalog")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert declares_nothing in res.stderr


def test_timeout_is_refused_before_it_is_written_into_a_profile(
    hsql: Hsql, declares_nothing: str, init_dirs: tuple[Path, Path]
) -> None:
    """A profile pairing an adapter with a timeout it cannot honor is one no
    run would start under."""
    cwd, _ = init_dirs
    res = hsql(
        "--config", "init", "-P", "prod", "-a", declares_nothing, "--timeout", "5"
    )
    assert res.exit_code == ExitCode.USAGE
    assert declares_nothing in res.stderr
    assert not (cwd / ".harlequin.toml").exists()


def test_config_validate_reports_a_timeout_the_adapter_cannot_do(
    hsql: Hsql, declares_nothing: str, config_dirs: tuple[Path, Path]
) -> None:
    cwd, _ = config_dirs
    (cwd / ".harlequin.toml").write_text(
        f"[profiles.prod]\nadapter = '{declares_nothing}'\ntimeout = 30\n"
    )

    res = hsql("--config", "validate", "-a", declares_nothing, "-tA")
    assert res.exit_code == ExitCode.USAGE
    file, key, problem, _ = res.stdout.strip().split("|")
    assert file == str(cwd / ".harlequin.toml")
    assert key == "profiles.prod.timeout"
    assert declares_nothing in problem


@pytest.mark.parametrize(
    "mode", [["--info"], ["--spec"], ["--config", "show"], ["--config", "validate"]]
)
def test_timeout_does_not_refuse_a_mode_that_reads_no_database(
    hsql: Hsql, declares_nothing: str, mode: list[str], config_dirs: tuple[Path, Path]
) -> None:
    """None of them waits on a database, and two of them are where a caller
    finds out which adapter they are on."""
    res = hsql(*mode, "-a", declares_nothing, "--timeout", "30")
    assert res.exit_code == ExitCode.OK, res.stderr


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_timeout_takes_a_positive_number_of_seconds(
    hsql: Hsql, duck: list[str], value: str
) -> None:
    res = hsql(*duck, "--timeout", value, "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""


@pytest.mark.parametrize("value", ["0", "true", "'soon'"])
def test_a_profile_takes_a_positive_number_of_seconds_too(
    hsql: Hsql, tmp_path: Path, value: str
) -> None:
    """click vets what was typed; a config file can say anything."""
    path = tmp_path / ".harlequin.toml"
    path.write_text(f"[profiles.prod]\nadapter = 'duckdb'\ntimeout = {value}\n")
    res = hsql("--config-path", str(path), "-P", "prod", ":memory:", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert res.stdout == ""
    assert "--timeout" in res.stderr


def test_every_bundled_adapter_declares_cancel(hsql: Hsql) -> None:
    """`--info` is where a caller learns which adapters can be timed out."""
    adapters = info_of(hsql("--info"))["adapters"]
    assert all(
        adapters[name]["capabilities"]["implements_cancel"]
        for name in ("duckdb", "sqlite")
    )


def test_timeout_is_in_the_help(hsql: Hsql) -> None:
    res = hsql("--help")
    assert res.exit_code == ExitCode.OK
    assert "--timeout" in res.output


UNSTOPPABLE = """
import sys, threading
from unittest.mock import MagicMock

import harlequin.plugins
import harlequin.hsql.timeout as timeout
from harlequin.adapter import HarlequinAdapter

timeout.GRACE_SECONDS = 0.5


class Cursor:
    def columns(self): return [("n", "##")]
    def set_limit(self, limit): return self
    def fetchall(self): threading.Event().wait(60)


class Connection:
    def execute(self, query): return Cursor()
    def get_catalog(self): raise NotImplementedError
    def cancel(self): pass


class Unstoppable(HarlequinAdapter):
    ADAPTER_OPTIONS = None
    IMPLEMENTS_CANCEL = True
    def __init__(self, conn_str, **options): pass
    def connect(self): return Connection()


entry_point = MagicMock()
entry_point.name = "unstoppable"
entry_point.load.return_value = Unstoppable
harlequin.plugins.entry_points = lambda group: [entry_point]

sys.argv = ["hsql", "-a", "unstoppable", "--timeout", "0.2", "-c", "select 1"]
from harlequin.hsql import main
main()
"""
"""An adapter that declares it can cancel and then does not stop."""


def test_a_run_that_outlasts_the_grace_period_still_exits_4(
    tmp_path: Path, clean_env: dict[str, str]
) -> None:
    """`sys.exit()` around a thread still inside a driver aborts the interpreter
    and exits 134, which is not a code hsql documents -- so the grace period
    ends in `os._exit()` instead. Only a real subprocess can show the code.
    """
    proc = subprocess.run(
        [sys.executable, "-c", UNSTOPPABLE],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        cwd=tmp_path,  # out of the repo, whose own .harlequin.toml would apply
        env=clean_env,
    )
    assert proc.returncode == ExitCode.TIMEOUT, proc.stderr
    assert proc.stdout == ""
    assert "timed out after 0.2s" in proc.stderr


def test_timeout_is_hsqls_where_an_adapter_declares_the_spelling(hsql: Hsql) -> None:
    """SQLite's own `timeout` is how long to wait for a locked table, and hsql's
    own flags are the frozen part -- so `--timeout` here is the deadline, and
    the adapter's is `--lock-timeout`."""
    res = hsql(
        "-a",
        "sqlite",
        ":memory:",
        "--timeout",
        "60",
        "--lock-timeout",
        "1",
        "-tAc",
        "select 1",
    )
    assert res.exit_code == ExitCode.OK, res.stderr
    assert res.stdout == "1\n"


def test_a_timed_out_script_does_not_report_a_missing_result_set(
    hsql: Hsql, stuck_adapter: list[_StuckConnection]
) -> None:
    """A cancelled script produced fewer result sets than it was going to, and
    the deadline is what a caller has to hear about rather than the count."""
    res = hsql(
        "-a", "stuck", "--timeout", "0.1", "--result", "2", "-c", "select 1; select 2"
    )
    assert res.exit_code == ExitCode.TIMEOUT
    assert "timed out after 0.1s" in res.stderr
    assert "result sets" not in res.stderr


def test_a_real_duckdb_query_times_out_and_says_so(
    tmp_path: Path, clean_env: dict[str, str]
) -> None:
    """The fakes above pin the attribution; this pins the interrupt itself.

    A subprocess because it is the only place the exit code is real: an
    aggregate over fifty billion rows is one every machine is still working on
    a second in.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from harlequin.hsql import main; sys.argv = sys.argv[1:]; "
            "main()",
            "hsql",
            *["-a", "duckdb", "--no-init", ":memory:"],
            *["--timeout", "1", "-c", "select sum(i) from range(50000000000) t(i)"],
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        cwd=tmp_path,  # out of the repo, whose own .harlequin.toml would apply
        env=clean_env,
    )
    assert proc.returncode == ExitCode.TIMEOUT, proc.stderr
    # the empty result set a cancelled DuckDB cursor returns, not printed
    assert proc.stdout == ""
    assert "timed out after 1s" in proc.stderr


# --- a bug in hsql itself ----------------------------------------------------


def run_main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Call the console script's entry point, which is where crashes are caught."""
    from harlequin.hsql import main

    monkeypatch.setattr(sys, "argv", ["hsql", *argv])
    with pytest.raises(SystemExit) as exit_info:
        main()
    return cast(int, exit_info.value.code)


@pytest.fixture
def hsql_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug in hsql, before anything it runs has had a chance to catch it."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("a bug in hsql")

    monkeypatch.setattr("harlequin.hsql.cli.build_cli", _boom)


def test_a_bug_in_hsql_is_not_reported_as_a_failed_query(
    monkeypatch: pytest.MonkeyPatch,
    hsql_crashes: None,
    crash_reports_go_to_tmp: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """1 is what a database rejecting the SQL means. A caller scripting against
    these could not otherwise tell the two apart."""
    code = run_main(monkeypatch, "-c", "select 1")

    assert code == ExitCode.CRASH
    assert code != ExitCode.QUERY

    (report,) = list(crash_reports_go_to_tmp.glob("crash-*.log"))
    assert "a bug in hsql" in report.read_text()

    stderr = capsys.readouterr().err
    assert "hsql hit a bug in itself" in stderr
    assert "please report this crash to help improve Harlequin" in stderr
    # the ask comes before the file it needs, and neither is called a review
    assert stderr.index(ISSUE_URL) < stderr.index(str(report))
    assert "review" not in stderr.lower()


def test_a_crash_report_masks_a_dsn_typed_on_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
    hsql_crashes: None,
    crash_reports_go_to_tmp: Path,
) -> None:
    """A crash during parsing happens before `hide_secrets_in()` runs, so the
    span-masking is the only thing that catches it."""
    run_main(monkeypatch, "postgres://tco:hunter2-and-more@warehouse:5432/analytics")

    (report,) = list(crash_reports_go_to_tmp.glob("crash-*.log"))
    text = report.read_text()
    assert "hunter2-and-more" not in text
    # and the rest of the DSN survives, which is what makes the argv worth having
    assert "warehouse:5432/analytics" in text


def test_a_crash_report_masks_a_credential_typed_into_the_query(
    monkeypatch: pytest.MonkeyPatch,
    hsql_crashes: None,
    crash_reports_go_to_tmp: Path,
) -> None:
    """The argv holds the statement `-c` was given, and a statement can carry a
    credential that no option describes and no caller registered."""
    run_main(
        monkeypatch, "-c", "create secret (type s3, secret 'sEcReT-value'); select 1"
    )

    (report,) = list(crash_reports_go_to_tmp.glob("crash-*.log"))
    text = report.read_text()
    assert "sEcReT-value" not in text
    # and the rest of the statement survives
    assert "create secret (type s3" in text


def test_a_crash_report_that_cannot_be_written_still_exits_70(
    monkeypatch: pytest.MonkeyPatch,
    hsql_crashes: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("the log dir is gone")

    monkeypatch.setattr("harlequin.crash.write_crash_report", _raise)

    assert run_main(monkeypatch, "-c", "select 1") == ExitCode.CRASH
    assert "hsql hit a bug in itself" in capsys.readouterr().err


def test_an_interrupt_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def _interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("harlequin.hsql.cli.build_cli", _interrupt)

    assert run_main(monkeypatch, "-c", "select 1") == ExitCode.INTERRUPT


def test_a_bug_in_the_session_client_is_a_crash_too(
    monkeypatch: pytest.MonkeyPatch,
    crash_reports_go_to_tmp: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The warm path is hsql's too, so a bug in it exits 70 rather than 1."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("a bug in the client")

    monkeypatch.setattr("harlequin.hsql.session.requested_session", _boom)

    assert (
        run_main(monkeypatch, "--session", "warm", "-c", "select 1") == ExitCode.CRASH
    )

    (report,) = list(crash_reports_go_to_tmp.glob("crash-*.log"))
    assert "a bug in the client" in report.read_text()
    assert "hsql hit a bug in itself" in capsys.readouterr().err


# --- the query log -----------------------------------------------------------


def logged(store: Path) -> list[dict[str, Any]]:
    """Every row hsql wrote, oldest first."""
    db = sqlite3.connect(store)
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute("select * from queries order by id")]
    finally:
        db.close()


def test_a_run_records_every_statement(
    hsql: Hsql, duck: list[str], query_log_path: Path
) -> None:
    res = hsql(*duck, "-c", "create table t (a int); select 1 as a; select nope")
    assert res.exit_code == ExitCode.QUERY
    recorded = logged(query_log_path)
    assert [row["sql"] for row in recorded] == [
        "create table t (a int);",
        "select 1 as a;",
        "select nope",
    ]
    assert [row["status"] for row in recorded] == ["ok", "ok", "error"]
    assert [row["program"] for row in recorded] == ["hsql"] * 3
    assert recorded[1]["rows"] == 1
    assert recorded[2]["error"]
    # one connection, one id, whatever the statement did
    assert len({row["connection"] for row in recorded}) == 1


def test_a_truncated_result_is_recorded_as_truncated(
    hsql: Hsql, duck: list[str], query_log_path: Path
) -> None:
    res = hsql(*duck, "--limit", "3", "-c", TEN_ROWS)
    assert res.exit_code == ExitCode.OK
    (row,) = logged(query_log_path)
    assert (row["rows"], row["truncated"]) == (3, 1)


def test_a_statement_whose_rows_were_never_fetched_is_still_recorded(
    hsql: Hsql, duck: list[str], query_log_path: Path
) -> None:
    """`--result last` declines to pay for the rest; they still ran."""
    res = hsql(*duck, "--result", "last", "-c", "select 1; select 2")
    assert res.exit_code == ExitCode.OK
    first, second = logged(query_log_path)
    assert (first["status"], first["rows"]) == ("ok", None)
    assert (second["status"], second["rows"]) == ("ok", 1)


def test_a_run_can_be_told_not_to_record_itself(
    hsql: Hsql, duck: list[str], query_log_path: Path
) -> None:
    res = hsql(*duck, "--no-write-history", "-c", "select 1")
    assert res.exit_code == ExitCode.OK
    assert not query_log_path.exists()


def test_a_profile_can_turn_the_log_off(
    hsql: Hsql, tmp_path: Path, query_log_path: Path
) -> None:
    config = tmp_path / ".harlequin.toml"
    config.write_text(
        "[profiles.quiet]\n"
        "adapter = 'duckdb'\n"
        "conn_str = [ ':memory:' ]\n"
        "no_init = true\n"
        "no_write_history = true\n"
    )
    res = hsql("--config-path", str(config), "-P", "quiet", "-c", "select 1")
    assert res.exit_code == ExitCode.OK
    assert not query_log_path.exists()


def test_a_profile_that_says_nothing_records_the_run(
    hsql: Hsql, tmp_path: Path, query_log_path: Path
) -> None:
    """The flag only turns recording off: leaving it out is the default."""
    config = tmp_path / ".harlequin.toml"
    config.write_text(
        "[profiles.loud]\n"
        "adapter = 'duckdb'\n"
        "conn_str = [ ':memory:' ]\n"
        "no_init = true\n"
    )
    res = hsql("--config-path", str(config), "-P", "loud", "-c", "select 1")
    assert res.exit_code == ExitCode.OK
    assert len(logged(query_log_path)) == 1


def test_no_write_history_that_is_not_a_boolean_is_a_usage_error(
    hsql: Hsql, tmp_path: Path
) -> None:
    """`no_write_history = "false"` read as true is a wrong a user cannot see."""
    config = tmp_path / ".harlequin.toml"
    config.write_text(
        "[profiles.odd]\n"
        "adapter = 'duckdb'\n"
        "conn_str = [ ':memory:' ]\n"
        "no_write_history = 'sometimes'\n"
    )
    res = hsql("--config-path", str(config), "-P", "odd", "-c", "select 1")
    assert res.exit_code == ExitCode.USAGE
    assert "no_write_history=" in res.stderr


def test_a_log_that_cannot_be_written_does_not_fail_the_query(
    hsql: Hsql, duck: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Said once on stderr, because stdout belongs to the query's output."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")
    monkeypatch.setattr(
        "harlequin.query_log.default_path", lambda: blocked / "history.db"
    )
    res = hsql(*duck, "-tA", "-c", "select 1")
    assert res.exit_code == ExitCode.OK
    assert res.stdout == "1\n"
    assert "query log" in res.stderr


def test_a_secret_never_reaches_the_query_log(
    hsql: Hsql, tmp_path: Path, query_log_path: Path
) -> None:
    """A profile's declared secret is masked in the SQL that mentions it."""
    config = tmp_path / ".harlequin.toml"
    config.write_text(
        "[profiles.duck]\n"
        "adapter = 'duckdb'\n"
        "conn_str = [ ':memory:' ]\n"
        "no_init = true\n"
        "md_token = 'hunter2-and-then-some'\n"
    )
    res = hsql(
        "--config-path",
        str(config),
        "-P",
        "duck",
        "-c",
        "select 'hunter2-and-then-some' as a",
    )
    assert res.exit_code == ExitCode.OK
    (row,) = logged(query_log_path)
    assert "hunter2-and-then-some" not in row["sql"]


def _timed_out_run(
    args: Sequence[str], *, clean_env: dict[str, str], tmp_path: Path
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    """Run hsql in a child and read back what it recorded.

    A child because a run that will not stop within the grace period ends in
    `os._exit()`, which in process would take the xdist worker with it.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from harlequin.hsql import main; sys.argv = sys.argv[1:]; "
            "main()",
            "hsql",
            *args,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env=clean_env,
    )
    # wherever platformdirs put it on this platform; `clean_env` keeps it here
    (store,) = tmp_path.rglob("history.db")
    return proc, logged(store)


def test_a_statement_cancelled_while_it_runs_is_still_recorded(
    tmp_path: Path, clean_env: dict[str, str]
) -> None:
    """SQLite steps the query inside `execute()`, so the timeout lands there.

    A lazy adapter is cancelled in the fetch instead; the row has to exist
    either way, or the history depends on which driver ran the query.
    """
    proc, recorded = _timed_out_run(
        [
            *["-a", "sqlite", ":memory:"],
            *["--timeout", "0.3"],
            "-c",
            "with recursive t(n) as (select 1 union all select n + 1 from t) "
            "select count(*) from t",
        ],
        clean_env=clean_env,
        tmp_path=tmp_path,
    )
    assert proc.returncode == ExitCode.TIMEOUT, proc.stderr
    (row,) = recorded
    assert row["status"] == "canceled"


def test_every_statement_a_cancel_stopped_says_so(
    tmp_path: Path, clean_env: dict[str, str]
) -> None:
    """Not just the one in flight: nothing after it was fetched either.

    The slow statement is in the middle, so a fix that marks only the statement
    the clock caught leaves the last one `ok` with no rows -- which is what
    `--result last` looks like.
    """
    proc, recorded = _timed_out_run(
        [
            *["-a", "duckdb", "--no-init", ":memory:"],
            *["--timeout", "0.3"],
            "-c",
            "select 1; select sum(i) from range(50000000000) t(i); select 2",
        ],
        clean_env=clean_env,
        tmp_path=tmp_path,
    )
    assert proc.returncode == ExitCode.TIMEOUT, proc.stderr
    assert len(recorded) == 3
    assert [row["status"] for row in recorded] == ["ok", "canceled", "canceled"]
    # the one that did finish keeps what it returned
    assert recorded[0]["rows"] == 1


def test_the_two_commands_key_one_connection_the_same_way(
    hsql: Hsql, tmp_path: Path, query_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One store with two writers is only one history if the ids agree.

    Against an adapter that declares no `connection_id`, which is the ABC's
    default and so the case that actually hashes: a profile that sets hsql's
    own keys, and a `-c` typed on the command line, must not move the id the
    IDE derives from the same profile.
    """
    from harlequin_duckdb import DuckDbAdapter

    # the property, replaced by the None every adapter that does not override
    # it returns -- which is what sends both commands through the hash
    monkeypatch.setattr(DuckDbAdapter, "connection_id", None)

    config = tmp_path / ".harlequin.toml"
    config.write_text(
        "[profiles.shared]\n"
        "adapter = 'duckdb'\n"
        "conn_str = [ ':memory:' ]\n"
        "no_init = true\n"
        "limit = 5\n"
        "format = 'csv'\n"
        "theme = 'fruity'\n"
        "no_download_tzdata = true\n"
        "no_write_history = false\n"
    )
    res = hsql("--config-path", str(config), "-P", "shared", "-c", "select 1")
    assert res.exit_code == ExitCode.OK
    (row,) = logged(query_log_path)
    assert row["connection"], "an unset id would make this test vacuous"

    app = MagicMock()
    # the command exits with it, and a MagicMock is not an exit code
    app.return_value.return_code = 0
    monkeypatch.setattr("harlequin.cli.Harlequin", app)
    from harlequin.cli import build_cli as ide_cli

    argv = ["--config-path", str(config), "-P", "shared"]
    ide = CliRunner().invoke(ide_cli(argv), argv, catch_exceptions=False)
    assert ide.exit_code == 0
    assert app.call_args.kwargs["connection_hash"] == row["connection"]


def test_the_sql_to_run_is_not_part_of_the_connections_id(
    hsql: Hsql, query_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two queries against one database are one connection, not two."""
    from harlequin_duckdb import DuckDbAdapter

    monkeypatch.setattr(DuckDbAdapter, "connection_id", None)

    for sql in ("select 1", "select 2"):
        assert hsql("-a", "duckdb", "--no-init", ":memory:", "-c", sql).exit_code == (
            ExitCode.OK
        )
    first, second = logged(query_log_path)
    assert first["connection"] == second["connection"]
