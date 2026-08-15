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
from harlequin.hsql.cli import build_cli
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
    real = harlequin.config.get_profile

    def counting(config_path: Any, profile_name: Any) -> Any:
        reads.append(config_path)
        return real(config_path=config_path, profile_name=profile_name)

    monkeypatch.setattr("harlequin.hsql.cli.get_profile", counting)
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
