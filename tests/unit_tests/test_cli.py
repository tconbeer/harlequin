import re
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from harlequin import Harlequin
from harlequin.cli import (
    DEFAULT_KEYMAP_NAMES,
    DEFAULT_LIMIT,
    DEFAULT_THEME,
    HEADLESS_DOCS_URL,
    HSQL_ONLY_OPTIONS,
    build_cli,
)
from harlequin.config import Config
from harlequin_duckdb import DUCKDB_OPTIONS, DuckDbAdapter
from harlequin_sqlite import SQLITE_OPTIONS, HarlequinSqliteAdapter


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
    config: Config = {"profiles": {"test-profile": {"theme": "fruity"}}}
    monkeypatch.setattr("harlequin.config.load_config", lambda *_: config)
    return config


def test_help(mock_adapter: MagicMock, mock_empty_config: None) -> None:
    """--help has to actually render.

    It is the one command that exercises rich-click's help rendering, so a
    rich-click that is out of step with click's API breaks it and nothing else.
    """
    runner = CliRunner()
    # the option groups are keyed on the program name, so this has to match the
    # name the console script is installed under
    res = runner.invoke(build_cli(), args="--help", prog_name="harlequin")
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
    res = runner.invoke(build_cli(), args=harlequin_args)
    assert res.exit_code == 0
    expected_conn_str = (harlequin_args,) if harlequin_args else tuple()
    mock_adapter.assert_called_once_with(conn_str=expected_conn_str)
    mock_harlequin.assert_called_once_with(
        adapter=mock_adapter.return_value,
        profile_name=None,
        connection_hash=mock_adapter.return_value.connection_id,
        max_results=DEFAULT_LIMIT,
        keymap_names=DEFAULT_KEYMAP_NAMES,
        user_defined_keymaps=[],
        theme=DEFAULT_THEME,
        show_files=None,
        show_s3=None,
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
    res = runner.invoke(build_cli(), args=harlequin_args)
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
    res = runner.invoke(build_cli(), args=harlequin_args)
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
    res = runner.invoke(build_cli(), args=harlequin_args)
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["theme"] == "one-dark"


@pytest.mark.parametrize(
    "harlequin_args",
    [
        "--limit 10",
        "-l 1000000",
        ":memory: -l 10",
        "foo.db --limit 5000000000",
        "--limit 0",
    ],
)
def test_limit(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = runner.invoke(build_cli(), args=harlequin_args)
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["max_results"] != 100_000


@pytest.mark.parametrize("harlequin_args", ["--show-files .", "-f .", "foo.db -f ."])
def test_show_files(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    harlequin_args: str,
    mock_empty_config: None,
) -> None:
    runner = CliRunner()
    res = runner.invoke(build_cli(), args=harlequin_args)
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["show_files"] == Path(".")


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
    res = runner.invoke(build_cli(), args=harlequin_args)
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
    res = runner.invoke(build_cli(), args=harlequin_args)
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
    res = runner.invoke(build_cli(), args=harlequin_args)
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
    res = runner.invoke(build_cli(), args=harlequin_args)
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
    res = runner.invoke(build_cli(), args=harlequin_args)
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
    res = runner.invoke(build_cli(), args=f"--config-path {config_path.as_posix()}")
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    # should use default profile of my-duckdb-profile
    assert mock_harlequin.call_args.kwargs["max_results"] == 200_000
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
    res = runner.invoke(
        build_cli(), env={"HARLEQUIN_CONFIG_PATH": config_path.as_posix()}
    )
    assert res.exit_code == 0
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    # should use default profile of my-duckdb-profile
    assert mock_harlequin.call_args.kwargs["max_results"] == 200_000
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
    res = runner.invoke(
        build_cli(), args=f"--config-path {config_path.as_posix()} other.db"
    )
    assert res.exit_code == 0
    mock_adapter.assert_called_once()
    assert mock_adapter.call_args.kwargs["conn_str"] == ("other.db",)
    # unrelated profile values are untouched
    assert mock_adapter.call_args.kwargs["extension"] == ["httpfs", "spatial"]
    assert mock_harlequin.call_args.kwargs["max_results"] == 200_000


def test_bad_config_exits(
    mock_harlequin: MagicMock,
    mock_adapter: MagicMock,
    data_dir: Path,
) -> None:
    runner = CliRunner()
    config_path = data_dir / "unit_tests" / "config" / "default_no_exist.toml"
    res = runner.invoke(build_cli(), args=f"--config-path {config_path.as_posix()}")
    assert res.exit_code == 2
    key_words = ["default_profile", "foo"]
    assert all([w in res.stderr for w in key_words])


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
    res = runner.invoke(
        build_cli(), args=f"-a sqlite --extension {extension_path.as_posix()}"
    )
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
    res = runner.invoke(
        build_cli(), args=f"-a sqlite --extension {extension_path.as_posix()}"
    )
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
    res = runner.invoke(build_cli(), args=[option, "select 1"])
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
    res = runner.invoke(build_cli(), args=["--thmee", "nord"])
    assert res.exit_code == 2
    said = _said(res.stderr)
    assert "No such option: --thmee" in said
    assert "hsql" not in said


def test_the_hsql_option_list_is_hsqls(
    monkeypatch: pytest.MonkeyPatch, mock_empty_config: None
) -> None:
    """What the hint is exposed to, since it is a copy rather than a lookup.

    With no adapter installed, both commands carry only their own options, so
    the difference between them is exactly the set worth pointing at.
    """
    from harlequin.hsql.cli import build_cli as build_hsql

    monkeypatch.setattr("harlequin.plugins.entry_points", lambda **_: [])

    def spellings(command: click.Command) -> set[str]:
        return {
            opt
            for param in command.params
            for opt in [*param.opts, *param.secondary_opts]
            if opt.startswith("-")
        }

    assert HSQL_ONLY_OPTIONS == spellings(build_hsql([])) - spellings(build_cli())
