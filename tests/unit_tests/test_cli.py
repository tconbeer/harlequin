from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from harlequin import Harlequin
from harlequin.adapter import DuckDBAdapter
from harlequin.cli import harlequin

DEFAULT_INIT_PATH = Path("~/.duckdbrc")
INIT_CONTENTS = "select 1;"


@pytest.fixture()
def mock_adapter(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock(spec=DuckDBAdapter)
    monkeypatch.setattr("harlequin.cli.DuckDBAdapter", mock)
    return mock


@pytest.fixture()
def mock_harlequin(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock(spec=Harlequin)
    monkeypatch.setattr("harlequin.cli.Harlequin", mock)
    return mock


@pytest.mark.parametrize("harlequin_args", ["", ":memory:"])
def test_default(
    mock_harlequin: MagicMock, mock_adapter: MagicMock, harlequin_args: str
) -> None:
    runner = CliRunner()
    runner.invoke(harlequin, args=harlequin_args)
    mock_adapter.assert_called_once_with(
        conn_str=(harlequin_args,) if harlequin_args else tuple(),
        init_path=DEFAULT_INIT_PATH,
        no_init=False,
        read_only=False,
        allow_unsigned_extensions=False,
        extension=(),
        force_install_extensions=False,
        custom_extension_repo=None,
        md_token=None,
        md_saas=False,
    )
    mock_harlequin.assert_called_once_with(
        adapter=mock_adapter.return_value,
        max_results=100_000,
        theme="monokai",
    )


@pytest.mark.parametrize(
    "harlequin_args", ["--init-path foo", ":memory: -i foo", "-init foo"]
)
def test_custom_init_script(mock_adapter: MagicMock, harlequin_args: str) -> None:
    runner = CliRunner()
    runner.invoke(harlequin, args=harlequin_args)
    mock_adapter.assert_called_once()
    assert mock_adapter.call_args
    assert mock_adapter.call_args.kwargs["init_path"] == Path("foo")


@pytest.mark.parametrize("harlequin_args", ["--no-init", ":memory: --no-init"])
def test_no_init_script(mock_adapter: MagicMock, harlequin_args: str) -> None:
    runner = CliRunner()
    runner.invoke(harlequin, args=harlequin_args)
    mock_adapter.assert_called_once()
    assert mock_adapter.call_args
    assert mock_adapter.call_args.kwargs["init_path"] == DEFAULT_INIT_PATH
    assert mock_adapter.call_args.kwargs["no_init"] is True


@pytest.mark.parametrize(
    "harlequin_args", ["--theme one-dark", ":memory: -t one-dark", "foo.db -t one-dark"]
)
def test_theme(mock_harlequin: MagicMock, harlequin_args: str) -> None:
    runner = CliRunner()
    runner.invoke(harlequin, args=harlequin_args)
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
def test_limit(mock_harlequin: MagicMock, harlequin_args: str) -> None:
    runner = CliRunner()
    runner.invoke(harlequin, args=harlequin_args)
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["max_results"] != 100_000
