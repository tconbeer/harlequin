import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from harlequin import Harlequin
from harlequin.cli import build_cli
from harlequin.config import Config
from harlequin_duckdb import DUCKDB_OPTIONS, DuckDbAdapter
from harlequin_sqlite import SQLITE_OPTIONS, HarlequinSqliteAdapter


@pytest.fixture()
def mock_adapter(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_adapter = MagicMock(name="mock_duckdb_adapter", spec=DuckDbAdapter)
    mock_adapter.ADAPTER_OPTIONS = DUCKDB_OPTIONS
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
def mock_empty_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("harlequin.cli.get_config_for_profile", lambda **_: dict())
    return None


@pytest.fixture()
def mock_load_config(monkeypatch: pytest.MonkeyPatch) -> Config:
    config: Config = {"profiles": {"test-profile": {"theme": "fruity"}}}
    monkeypatch.setattr("harlequin.config.load_config", lambda *_: config)
    return config


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
        connection_hash=hashlib.md5(
            json.dumps({"conn_str": expected_conn_str}).encode("utf-8")
        )
        .digest()
        .hex(),
        max_results=100_000,
        theme="harlequin",
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
    assert all([w in res.stdout for w in key_words])


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
    assert all([w in res.stdout for w in key_words])


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
    assert all([w in res.stdout for w in key_words])


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
    assert "No such option" in res.stdout
