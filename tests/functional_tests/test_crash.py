"""What a user sees, and what survives, when Harlequin hits a bug in itself.

Injected through `_handle_exception` directly: the handler is the contract, and
finding a real crash to provoke would pin the test to whichever bug it used.
The assertions are on stderr, because that is where the message lands -- the
message pump is gone by then, so there is no screen to look at.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest
from rich.console import Console
from textual.widgets.text_area import Selection

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.app_base import _as_markup
from harlequin.crash import ISSUE_URL, crash_message
from harlequin.editor_cache import BufferState, Cache, get_cache_file
from harlequin.exception import HarlequinCrashError, pretty_error_message


@pytest.fixture(autouse=True)
def mock_user_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr("harlequin.editor_cache.user_cache_dir", lambda **_: cache_dir)
    return cache_dir


def crash(app: Harlequin, error: Exception) -> None:
    """Hand the handler an exception the way the message pump does.

    From inside an `except` block: Textual's own renderer reads
    `sys.exc_info()`, so an exception injected from outside one would exercise
    a path no crash takes.
    """
    try:
        raise error
    except Exception as caught:
        app._handle_exception(caught)


def panel_text(capsys: pytest.CaptureFixture[str]) -> str:
    """What was printed, with the box drawn around it taken off."""
    return "\n".join(
        line.strip("│╭╮╰╯─ \t") for line in capsys.readouterr().err.splitlines()
    )


def unwrapped(text: str) -> str:
    """The same, on one line: rich folds a long path across two of them."""
    return "".join(text.splitlines())


@pytest.mark.asyncio
async def test_a_crash_prints_a_panel_and_not_a_traceback(
    app: Harlequin, crash_reports_go_to_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(RuntimeError):
        async with app.run_test() as pilot:
            while app.editor is None:
                await pilot.pause()
            crash(app, RuntimeError("boom"))
            await pilot.pause()

    assert app.return_code == 1

    (report,) = list(crash_reports_go_to_tmp.glob("crash-*.log"))
    assert "RuntimeError" in report.read_text()

    printed = panel_text(capsys)
    assert "Harlequin crashed." in printed
    assert "RuntimeError: boom" in printed
    assert "Please report this crash to help improve Harlequin." in printed
    assert str(report) in unwrapped(printed)
    assert "Traceback (most recent call last)" not in printed


@pytest.mark.use_cache
@pytest.mark.asyncio
async def test_a_crash_saves_the_open_buffers(
    app: Harlequin,
    mock_user_cache_dir: Path,
    crash_reports_go_to_tmp: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(RuntimeError):
        async with app.run_test() as pilot:
            while app.editor is None:
                await pilot.pause()
            app.editor.text = "select 'work in progress'"
            crash(app, RuntimeError("boom"))
            await pilot.pause()

    (recovered,) = list(mock_user_cache_dir.glob("recovered-*.pickle"))
    cache = pickle.loads(recovered.read_bytes())
    assert cache.buffers[0].text == "select 'work in progress'"
    # the last clean quit's cache is untouched: if the recovered buffers are
    # themselves the problem, that is still on disk
    assert not get_cache_file().exists()

    assert "Your buffers have been saved" in panel_text(capsys)


@pytest.mark.asyncio
async def test_the_report_holds_what_the_session_was(
    duckdb_adapter: type[HarlequinAdapter], crash_reports_go_to_tmp: Path
) -> None:
    """Built the way `cli.py` builds it, which is where the adapter gets a name."""
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        adapter_name="duckdb",
        profile_name="warehouse",
    )
    with pytest.raises(RuntimeError):
        async with app.run_test() as pilot:
            while app.editor is None:
                await pilot.pause()
            app.editor.text = "select 'reproduce me'"
            crash(app, RuntimeError("boom"))
            await pilot.pause()

    (report,) = list(crash_reports_go_to_tmp.glob("crash-*.log"))
    text = report.read_text()
    assert "adapter: duckdb" in text
    assert "adapter_class: DuckDbAdapter" in text
    # the distribution, not the module: both bundled adapters ship inside
    # `harlequin`, whose name their module never spells
    assert "adapter_distribution: harlequin " in text
    assert "profile: warehouse" in text
    assert "connected: True" in text
    assert "buffers: 1" in text
    # the SQL is last, under a heading that says what it is
    assert text.rstrip().endswith("select 'reproduce me'")


@pytest.mark.asyncio
async def test_a_crash_report_is_written_once(
    app: Harlequin, crash_reports_go_to_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The handler runs inside an `except` block, so it must not be able to
    raise. A second exception returns without reporting again."""
    with pytest.raises(RuntimeError):
        async with app.run_test() as pilot:
            while app.editor is None:
                await pilot.pause()
            crash(app, RuntimeError("boom"))
            crash(app, RuntimeError("and again"))
            await pilot.pause()

    assert len(list(crash_reports_go_to_tmp.glob("crash-*.log"))) == 1
    printed = panel_text(capsys)
    assert printed.count("Harlequin crashed.") == 1
    assert "and again" not in printed


@pytest.mark.asyncio
async def test_a_crash_is_reported_even_when_the_report_cannot_be_written(
    app: Harlequin,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("the log dir is gone")

    monkeypatch.setattr("harlequin.app_base.write_crash_report", _raise)

    with pytest.raises(RuntimeError):
        async with app.run_test() as pilot:
            while app.editor is None:
                await pilot.pause()
            crash(app, RuntimeError("boom"))
            await pilot.pause()

    assert app.return_code == 1
    printed = panel_text(capsys)
    assert "RuntimeError: boom" in printed
    assert "Traceback (most recent call last)" not in printed


@pytest.mark.asyncio
async def test_the_traceback_is_still_printed_in_dev_mode(
    app: Harlequin, crash_reports_go_to_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`make serve`, i.e. `textual run --dev`: one code path, both audiences."""
    with pytest.raises(RuntimeError):
        async with app.run_test() as pilot:
            while app.editor is None:
                await pilot.pause()
            app.features = frozenset({*app.features, "debug"})
            crash(app, RuntimeError("boom"))
            await pilot.pause()

    printed = panel_text(capsys)
    assert "Harlequin crashed." in printed
    assert "Traceback (most recent call last)" in printed


def test_a_crash_before_the_app_is_mounted_saves_nothing(
    duckdb_adapter: type[HarlequinAdapter],
) -> None:
    """`editor_collection` is created in compose(), not __init__."""
    app = Harlequin(duckdb_adapter([":memory:"], no_init=True))

    assert app._save_work_on_crash() is False
    assert isinstance(app._crash_context(), dict)


@pytest.mark.use_cache
@pytest.mark.asyncio
async def test_a_crash_while_replaying_recovered_buffers_cannot_repeat(
    app: Harlequin,
    app_all_adapters: Harlequin,
    mock_user_cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_reports_go_to_tmp: Path,
) -> None:
    """The crash-loop guard, end to end: the poisoned file is spent by the
    start that choked on it."""
    poisoned = mock_user_cache_dir / "recovered-20260904T120000Z-99.pickle"
    poisoned.parent.mkdir(parents=True, exist_ok=True)
    poisoned.write_bytes(
        pickle.dumps(
            Cache(focus_index=0, buffers=[BufferState(Selection(), "select 1\n")])
        )
    )

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("crash while replaying")

    monkeypatch.setattr(
        "harlequin.components.code_editor.EditorCollection.action_new_buffer", _boom
    )
    with pytest.raises(RuntimeError):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

    assert not poisoned.exists()
    assert poisoned.with_suffix(".replayed").exists()

    monkeypatch.undo()
    async with app_all_adapters.run_test() as pilot:
        while app_all_adapters.editor is None:
            await pilot.pause()
        assert app_all_adapters.editor_collection is not None
        assert app_all_adapters.editor_collection.tab_count == 1
        assert app_all_adapters.editor.text == ""


def render_panel(message: str) -> str:
    """The panel as a terminal receives it, escape sequences and all."""
    console = Console(width=100, force_terminal=True, legacy_windows=False)
    with console.capture() as capture:
        console.print(
            pretty_error_message(
                HarlequinCrashError(_as_markup(message), title="Harlequin crashed.")
            )
        )
    return capture.get()


def test_the_reporting_url_is_a_clickable_link() -> None:
    """A bare URL is not clickable in every terminal; an OSC-8 link is."""
    message = crash_message(Path("/tmp/crash.log"), RuntimeError("boom"), saved=False)

    assert f"[link={ISSUE_URL}]{ISSUE_URL}[/link]" in _as_markup(message)

    rendered = render_panel(message)
    assert "\x1b]8;" in rendered  # the escape a terminal makes clickable
    assert ISSUE_URL in rendered  # and the URL still readable where it isn't


def test_an_exception_whose_text_looks_like_markup_survives() -> None:
    """The message quotes a driver, and a driver may put brackets in one."""
    message = crash_message(
        None, RuntimeError("no column [amount] in orders"), saved=False
    )

    assert "[amount]" in render_panel(message)
