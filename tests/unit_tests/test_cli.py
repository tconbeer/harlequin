from pathlib import Path
from typing import Any, Tuple
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from harlequin import Harlequin
from harlequin.cli import harlequin

DEFAULT_INIT_PATH = Path("~/.duckdbrc")
INIT_CONTENTS = "select 1;"


@pytest.fixture()
def mock_harlequin(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock(spec=Harlequin)
    monkeypatch.setattr("harlequin.cli.Harlequin", mock)
    return mock


@pytest.fixture()
def empty_init_script(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()

    def mock_get_init_script(path: Any, ignore: bool) -> Tuple[Path, str]:
        return (path, "")

    mock.side_effect = mock_get_init_script
    monkeypatch.setattr("harlequin.cli.get_init_script", mock)
    return mock


@pytest.fixture()
def basic_init_script(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()

    def mock_get_init_script(path: Any, ignore: bool) -> Tuple[Path, str]:
        return (path, "") if ignore else (path, INIT_CONTENTS)

    mock.side_effect = mock_get_init_script
    monkeypatch.setattr("harlequin.cli.get_init_script", mock)
    return mock


@pytest.mark.parametrize("harlequin_args", ["", ":memory:"])
def test_default(
    mock_harlequin: MagicMock, harlequin_args: str, empty_init_script: MagicMock
) -> None:
    runner = CliRunner()
    runner.invoke(harlequin, args=harlequin_args)
    mock_harlequin.assert_called_once_with(
        db_path=(":memory:",),
        init_script=(DEFAULT_INIT_PATH, ""),
        max_results=100_000,
        read_only=False,
        extensions=(),
        force_install_extensions=False,
        custom_extension_repo=None,
        theme="monokai",
        md_token=None,
        md_saas=False,
        allow_unsigned_extensions=False,
    )


@pytest.mark.parametrize("harlequin_args", ["", ":memory:"])
def test_init_script(
    mock_harlequin: MagicMock, harlequin_args: str, basic_init_script: MagicMock
) -> None:
    runner = CliRunner()
    runner.invoke(harlequin, args=harlequin_args)
    basic_init_script.assert_called_once_with(DEFAULT_INIT_PATH, False)
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["db_path"] == (":memory:",)
    assert mock_harlequin.call_args.kwargs["init_script"] == (
        DEFAULT_INIT_PATH,
        INIT_CONTENTS,
    )


@pytest.mark.parametrize(
    "harlequin_args", ["--init-path foo", ":memory: -i foo", "-init foo"]
)
def test_custom_init_script(
    mock_harlequin: MagicMock, harlequin_args: str, basic_init_script: MagicMock
) -> None:
    runner = CliRunner()
    runner.invoke(harlequin, args=harlequin_args)
    basic_init_script.assert_called_once_with(Path("foo"), False)
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["db_path"] == (":memory:",)
    assert mock_harlequin.call_args.kwargs["init_script"] == (
        Path("foo"),
        INIT_CONTENTS,
    )


@pytest.mark.parametrize("harlequin_args", ["--no-init", ":memory: --no-init"])
def test_no_init_script(
    mock_harlequin: MagicMock, harlequin_args: str, basic_init_script: MagicMock
) -> None:
    runner = CliRunner()
    runner.invoke(harlequin, args=harlequin_args)
    basic_init_script.assert_called_once_with(DEFAULT_INIT_PATH, True)
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["db_path"] == (":memory:",)
    assert mock_harlequin.call_args.kwargs["init_script"] == (DEFAULT_INIT_PATH, "")


@pytest.mark.parametrize(
    "harlequin_args", ["--theme one-dark", ":memory: -t one-dark", "foo.db -t one-dark"]
)
def test_theme(
    mock_harlequin: MagicMock, harlequin_args: str, empty_init_script: MagicMock
) -> None:
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
def test_limit(
    mock_harlequin: MagicMock, harlequin_args: str, empty_init_script: MagicMock
) -> None:
    runner = CliRunner()
    runner.invoke(harlequin, args=harlequin_args)
    mock_harlequin.assert_called_once()
    assert mock_harlequin.call_args
    assert mock_harlequin.call_args.kwargs["max_results"] != 100_000
