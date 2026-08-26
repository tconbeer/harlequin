import re
import shlex
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner, Result

from harlequin import Harlequin
from harlequin.cli import (
    DEFAULT_KEYMAP_NAMES,
    DEFAULT_THEME,
    DEFAULT_VIEWER_MAX_ROWS,
    HEADLESS_DOCS_URL,
    build_cli,
    hsql_profile_keys,
    hsql_spellings,
)
from harlequin.config import Config, Provenance, _merge
from harlequin_duckdb import DUCKDB_OPTIONS, DuckDbAdapter
from harlequin_sqlite import SQLITE_OPTIONS, HarlequinSqliteAdapter


def invoke(runner: CliRunner, args: str | list[str] = "", **kwargs: Any) -> Result:
    """Build the command for these arguments, then run it against them.

    The two are one step for a caller -- `harlequin()` does exactly this with
    `sys.argv` -- because the command is built from the arguments: which
    adapter's connection options it carries is what the first pass over them
    settles.
    """
    argv = shlex.split(args) if isinstance(args, str) else args
    return runner.invoke(build_cli(argv), args=argv, **kwargs)


@pytest.fixture()
def mock_adapter(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_adapter = MagicMock(name="mock_duckdb_adapter", spec=DuckDbAdapter)
    mock_adapter.ADAPTER_OPTIONS = DUCKDB_OPTIONS
    mock_adapter.profile_name = None
    mock_entrypoint = MagicMock(name="mock_entrypoint")
    mock_entrypoint.name = "duckdb"
    mock_entrypoint.load.return_value = mock_adapter
    mock_entry_points = MagicMock()
    mock_entry_points.return_value = [mock_entrypoint]
    monkeypatch.setattr("harlequin.plugins.entry_points", mock_entry_points)
    return mock_adapter


@pytest.fixture()
def mock_sqlite_adapter(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_adapter = MagicMock(name="mock_sqlite_adapter", spec=HarlequinSqliteAdapter)
    mock_adapter.ADAPTER_OPTIONS = SQLITE_OPTIONS
    mock_entrypoint = MagicMock(name="mock_entrypoint")
    mock_entrypoint.name = "sqlite"
    mock_entrypoint.load.return_value = mock_adapter
    mock_entry_points = MagicMock()
    mock_entry_points.return_value = [mock_entrypoint]
    monkeypatch.setattr("harlequin.plugins.entry_points", mock_entry_points)
    return mock_adapter


@pytest.fixture()
def mock_harlequin(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock(spec=Harlequin)
    monkeypatch.setattr("harlequin.cli.Harlequin", mock)
    return mock


@pytest.fixture()
def mock_empty_config(no_discovered_config: None) -> None:
    """No config file anywhere. See tests/conftest.py."""


@pytest.fixture()
def mock_load_config(monkeypatch: pytest.MonkeyPatch) -> Config:
    """A merged config, in place of whatever is on the machine running this.

    Merged by the real `_merge()` rather than handed over as a literal, because
    a caller that asks for a `Provenance` gets one filled in -- and a double
    that filled it in itself would be a second copy of the merge's bookkeeping.
    """
    config = Config(profiles={"test-profile": {"theme": "fruity"}})

    def load_config(
        config_path: Path | None = None, provenance: Provenance | None = None
    ) -> Config:
        merged = Config()
        _merge(config, into=merged, source=Path("test.toml"), provenance=provenance)
        return merged

    monkeypatch.setattr("harlequin.config.load_config", load_config)
    return config


def test_help(mock_adapter: MagicMock, mock_empty_config: None) -> None:
    """--help has to actually render.

    It is the one command that exercises rich-click's help rendering, so a
    rich-click that is out of step with click's API breaks it and nothing else.
    """
    runner = CliRunner()
    # the option groups are keyed on the program name, so this has to match the
    # name the console script is installed under
    res = invoke(runner, "--help", prog_name="harlequin")
    assert res.exception is None, f"--help raised {res.exception!r}"
    assert res.exit_code == 0
    # the option groups configured at the top of cli.py, including the ones
    # built dynamically from the installed adapters
    assert "Harlequin Options" in res.output
    assert "duckdb Adapter Options" in res.output
    # an option's help text, and a metavar appended to it
    assert "--theme" in res.output
    assert "(TEXT)" in res.output


@pytest.mark.parametrize("harlequin_args", ["", ":memory:"])
def test_default(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    expected_conn_str = (harlequin_args,) if harlequin_args else tuple()
    mock_adapter.assert_called_once_with(conn_str=expected_conn_str)
    mock_harlequin.assert_called_once_with(
        adapter=mock_adapter.return_value,
        profile_name=None,
        connection_hash=mock_adapter.return_value.connection_id,
        viewer_max_rows=DEFAULT_VIEWER_MAX_ROWS,
        query_limit=None,
        keymap_names=DEFAULT_KEYMAP_NAMES,
        user_defined_keymaps=[],
        theme=DEFAULT_THEME,
        show_files=None,
        show_s3=None,
        export_path=None,
    )


@pytest.mark.parametrize(
    "harlequin_args", ["--init-path foo", ":memory: -i foo", "-init foo"]
)
def test_custom_init_script(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    mock_adapter.assert_called_once()
    assert mock_adapter.call_args
    assert mock_adapter.call_args.kwargs["init_path"] == Path("foo").resolve()


@pytest.mark.parametrize("harlequin_args", ["--no-init", ":memory: --no-init"])
def test_no_init_script(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    mock_adapter.assert_called_once()
    assert mock_adapter.call_args
    assert mock_adapter.call_args.kwargs["no_init"] is True


@pytest.mark.parametrize(
    "harlequin_args", ["--theme one-dark", ":memory: -t one-dark", "foo.db -t one-dark"]
)
def test_theme(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["theme"] == "one-dark"


@pytest.mark.parametrize(
    ("harlequin_args", "expected"),
    [
        ("--viewer-max-rows 10", 10),
        (":memory: --viewer-max-rows 1000000", 1_000_000),
        ("foo.db --viewer-max-rows 5000000000", 5_000_000_000),
        # both spellings of "hold everything"
        ("--viewer-max-rows 0", None),
        ("--viewer-max-rows -1", None),
    ],
)
def test_viewer_max_rows(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    expected: int | None,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["viewer_max_rows"] == expected
    # the display cap says nothing about what is fetched
    assert mock_harlequin.call_args.kwargs["query_limit"] is None


@pytest.mark.parametrize(
    ("harlequin_args", "expected"),
    [
        ("--limit 10", 10),
        (":memory: --limit 10", 10),
        ("foo.db --limit 10", 10),
        # a header and no rows, which is how you ask what a query returns
        ("--limit 0", 0),
        # every row, which is what an unset limit does too
        ("--limit -1", None),
    ],
)
def test_limit_is_the_hard_fetch_limit(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    expected: int | None,
    mock_empty_config: None,
) -> None:
    """The same limit hsql applies, and the Run Query Bar shows it."""
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["query_limit"] == expected
    # the viewer's cap is untouched by it
    assert mock_harlequin.call_args.kwargs["viewer_max_rows"] == DEFAULT_VIEWER_MAX_ROWS


def test_unset_limit_fetches_everything(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    mock_empty_config: None,
) -> None:
    """Naming nothing is the full fetch the IDE has always done."""
    runner = CliRunner()
    res = invoke(runner, "")
    assert res.exit_code == 0
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["query_limit"] is None


def test_the_two_limits_are_independent(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = invoke(runner, "--limit 10 --viewer-max-rows 20")
    assert res.exit_code == 0
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["query_limit"] == 10
    assert mock_harlequin.call_args.kwargs["viewer_max_rows"] == 20


def test_a_profile_limit_is_a_fetch_limit(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    data_dir: Path,
) -> None:
    """The profile that motivated the split, read the new way.

    `limit = 200_000` in a config file both commands share now means the same
    thing in both: 200,000 rows leave the database, and nothing has an opinion
    about what the Results Viewer does with them.
    """
    runner = CliRunner()
    config_path = data_dir / "unit_tests" / "config" / "good_config.toml"
    res = invoke(runner, f"--config-path {config_path.as_posix()}")
    assert res.exit_code == 0
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["query_limit"] == 200_000
    assert mock_harlequin.call_args.kwargs["viewer_max_rows"] == DEFAULT_VIEWER_MAX_ROWS


@pytest.mark.parametrize("harlequin_args", ["--show-files .", "-f .", "foo.db -f ."])
def test_show_files(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["show_files"] == Path(".")


@pytest.mark.parametrize(
    "harlequin_args,export_path",
    [
        ("--output .harlequin", Path(".harlequin")),
        ("-o .harlequin/", Path(".harlequin")),
        ("-o exports/out.csv", Path("exports/out.csv")),
        ("", None),
    ],
)
def test_output_sets_the_export_path(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    export_path: Path | None,
    mock_empty_config: None,
) -> None:
    """`-o` is where the Data Exporter starts, whether it names a folder or a
    file."""
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["export_path"] == export_path


@pytest.mark.parametrize(
    "harlequin_args",
    [
        "--adapter duckdb",
        "-a duckdb",
        "-a DUCKDB",
    ],
)
def test_adapter_opt(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["adapter"] == mock_adapter.return_value


@pytest.mark.parametrize(
    "harlequin_args",
    [
        "--adapter foo",
        "-a bar",
    ],
)
def test_bad_adapter_opt(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 2
    key_words = ["Error", "Invalid", "-a", "-adapter", "duckdb"]
    assert all([w in res.stderr for w in key_words])


@pytest.mark.parametrize(
    "harlequin_args",
    [
        "--profile test-profile",
        "-P test-profile",
    ],
)
def test_profile_opt(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_load_config: Config,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["theme"] == "fruity"


@pytest.mark.parametrize(
    "harlequin_args",
    [
        "--profile test-profile -t zenburn",
        "-P test-profile --theme zenburn",
    ],
)
def test_profile_override(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_load_config: Config,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["theme"] == "zenburn"


@pytest.mark.parametrize(
    "harlequin_args",
    [
        "--profile foo",
        "-P bar",
    ],
)
def test_bad_profile_opt(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_load_config: Config,
) -> None:
    runner = CliRunner()
    res = invoke(runner, harlequin_args)
    assert res.exit_code == 2
    key_words = ["profile", "config"]
    assert all([w in res.stderr for w in key_words])


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_config_path(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    data_dir: Path,
    filename: str,
) -> None:
    runner = CliRunner()
    config_path = data_dir / "unit_tests" / "config" / filename
    res = invoke(runner, f"--config-path {config_path.as_posix()}")
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    # should use default profile of my-duckdb-profile
    assert mock_harlequin.call_args.kwargs["query_limit"] == 200_000
    mock_adapter.assert_called_once()
    assert mock_adapter.call_args.kwargs["conn_str"] == ["my-database.db"]
    assert mock_adapter.call_args.kwargs["read_only"] is False
    assert mock_adapter.call_args.kwargs["extension"] == ["httpfs", "spatial"]


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_config_path_fron_env(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    data_dir: Path,
    filename: str,
) -> None:
    runner = CliRunner()
    config_path = data_dir / "unit_tests" / "config" / filename
    res = invoke(runner, env={"HARLEQUIN_CONFIG_PATH": config_path.as_posix()})
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    # should use default profile of my-duckdb-profile
    assert mock_harlequin.call_args.kwargs["query_limit"] == 200_000
    mock_adapter.assert_called_once()
    assert mock_adapter.call_args.kwargs["conn_str"] == ["my-database.db"]
    assert mock_adapter.call_args.kwargs["read_only"] is False
    assert mock_adapter.call_args.kwargs["extension"] == ["httpfs", "spatial"]


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_conn_str_overrides_the_profile(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    data_dir: Path,
    filename: str,
) -> None:
    """A conn_str on the command line beats the profile's.

    test_config_path is the other half: with none passed, the profile's survives.
    """
    runner = CliRunner()
    config_path = data_dir / "unit_tests" / "config" / filename
    res = invoke(runner, f"--config-path {config_path.as_posix()} other.db")
    assert res.exit_code == 0
    mock_adapter.assert_called_once()
    assert mock_adapter.call_args.kwargs["conn_str"] == ("other.db",)
    # unrelated profile values are untouched
    assert mock_adapter.call_args.kwargs["extension"] == ["httpfs", "spatial"]
    assert mock_harlequin.call_args.kwargs["query_limit"] == 200_000


def test_bad_config_exits(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    data_dir: Path,
) -> None:
    runner = CliRunner()
    config_path = data_dir / "unit_tests" / "config" / "default_no_exist.toml"
    res = invoke(runner, f"--config-path {config_path.as_posix()}")
    assert res.exit_code == 2
    key_words = ["default_profile", "foo"]
    assert all([w in res.stderr for w in key_words])


def test_a_profile_naming_an_uninstalled_adapter_says_so(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    tmp_path: Path,
) -> None:
    """The one name the command's `--adapter` Choice never sees.

    click vets what is typed, so a name nothing installed provides can only
    arrive from a config file -- and that is the profile the pass over the
    arguments could not turn into an adapter.
    """
    config_path = tmp_path / ".harlequin.toml"
    config_path.write_text('[profiles.gone]\nadapter = "notreal"\n')
    runner = CliRunner()
    res = invoke(runner, f"--config-path {config_path.as_posix()} -P gone")
    assert res.exit_code == 2
    said = _said(res.stderr)
    assert "notreal" in said
    # and what it could have been
    assert "duckdb" in said


@pytest.mark.skipif(
    not hasattr(sqlite3.Connection, "enable_load_extension"),
    reason="Extension option not supported on many pythons.",
)
def test_sqlite_extensions(
    mock_harlequin: MagicMock,
    mock_sqlite_adapter: MagicMock,
    mock_empty_config: None,
    data_dir: Path,
) -> None:
    extension_path = data_dir / "unit_tests" / "sqlite_extension" / "hello0"
    runner = CliRunner()
    res = invoke(runner, f"-a sqlite --extension {extension_path.as_posix()}")
    assert res.exit_code == 0


@pytest.mark.skipif(
    hasattr(sqlite3.Connection, "enable_load_extension"),
    reason="Extension option not supported on many pythons.",
)
def test_sqlite_extension_not_supported(
    mock_harlequin: MagicMock,
    mock_sqlite_adapter: MagicMock,
    mock_empty_config: None,
    data_dir: Path,
) -> None:
    extension_path = data_dir / "unit_tests" / "sqlite_extension" / "hello0"
    runner = CliRunner()
    res = invoke(runner, f"-a sqlite --extension {extension_path.as_posix()}")
    assert res.exit_code == 2
    assert "No such option" in res.stderr


# --- pointing at the other command -------------------------------------------

_ANSI = re.compile(r"\x1b(?:\[[0-9;]*[a-zA-Z]|\][^\x1b\x07]*(?:\x1b\\|\x07))")


def _said(stderr: str) -> str:
    """What a reader sees, out of what rich-click wrote.

    rich-click renders errors into a panel at truecolor, so the message arrives
    styled, wrapped at whatever width the terminal happens to be, and with the
    option name colored -- which puts escape sequences in the middle of the
    sentence, not only around it.
    """
    return " ".join(_ANSI.sub("", stderr).split())


@pytest.mark.parametrize("option", ["-c", "--command", "--csv", "-A", "--null-string"])
def test_an_hsql_option_names_hsql(
    mock_adapter: MagicMock, mock_empty_config: None, option: str
) -> None:
    """The likeliest first mistake now that there are two commands."""
    runner = CliRunner()
    res = invoke(runner, [option, "select 1"])
    assert res.exit_code == 2
    # rich-click wraps the panel it renders errors in, so the message arrives
    # broken across lines at whatever width the terminal happens to be
    said = _said(res.stderr)
    assert f"{option} is not a harlequin option" in said
    assert f"Did you mean 'hsql {option}'?" in said
    assert HEADLESS_DOCS_URL in said


def test_an_unknown_option_that_is_not_hsqls_is_unchanged(
    mock_adapter: MagicMock, mock_empty_config: None
) -> None:
    """click's own message, and its own suggestion, for everything else."""
    runner = CliRunner()
    res = invoke(runner, ["--thmee", "nord"])
    assert res.exit_code == 2
    said = _said(res.stderr)
    # click has reworded this message across releases (8.4 quotes the option
    # and lists every near match), so assert on its parts rather than on one
    # release's sentence
    assert "No such option" in said
    assert "--thmee" in said
    # click's own suggestion, drawn from this command's options
    assert "--theme" in said
    assert "hsql" not in said


def test_what_this_command_reads_off_the_other_one(mock_empty_config: None) -> None:
    """Both are read from hsql's real command rather than copied from it.

    Which means asserting they are hsql's own surface and not an adapter's:
    building it must not import one, or the sets would grow with whatever
    happens to be installed.
    """
    assert "--csv" in hsql_spellings()
    assert "--theme" not in hsql_spellings()
    assert "--no-init" not in hsql_spellings(), "an adapter's option, not hsql's"

    assert {"format", "stats", "on_error"} <= hsql_profile_keys()
    assert "no_init" not in hsql_profile_keys()
