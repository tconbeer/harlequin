import sys
from contextlib import suppress
from typing import Awaitable, Callable
from unittest.mock import MagicMock

import pytest
from textual.pilot import Pilot
from textual.widgets import Input
from textual.widgets._tree import TreeNode
from textual.worker import WorkerCancelled

from harlequin.app import Harlequin
from harlequin.autocomplete import HarlequinCompletion
from harlequin.catalog import CatalogItem


@pytest.fixture(autouse=True)
def no_cursor_blink(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze every Input's cursor in the visible state.

    Snapshots are captured after a settle delay that is longer than the blink
    interval, so a blinking cursor makes snapshot tests nondeterministic.
    Neutering the toggle leaves the pause/restart behavior (and so the blurred
    state) intact.
    """
    monkeypatch.setattr(Input, "_toggle_cursor", lambda _self: None)


@pytest.fixture(autouse=True)
def no_use_buffer_cache(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if "use_cache" in request.keywords:
        return
    monkeypatch.setattr("harlequin.components.code_editor.load_cache", lambda: None)
    monkeypatch.setattr(
        "harlequin.components.code_editor.adopt_recovery", lambda: (None, None)
    )
    monkeypatch.setattr("harlequin.app.write_editor_cache", lambda *_: None)
    monkeypatch.setattr("harlequin.app.clear_recovery", lambda *_: None)


@pytest.fixture(autouse=True)
def no_use_catalog_cache(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if "use_cache" in request.keywords:
        return
    monkeypatch.setattr("harlequin.app.get_catalog_cache", lambda *_: None)
    monkeypatch.setattr("harlequin.app.update_catalog_cache", lambda *_: None)


@pytest.fixture(autouse=True)
def mock_config_loader(no_discovered_config: None) -> None:
    """Autouse wrapper over the shared fixture. test_keys_app overrides it."""


@pytest.fixture(autouse=True)
def mock_completions(monkeypatch: pytest.MonkeyPatch) -> None:
    KEYWORDS = [
        "abort",
        "all",
        "alter",
        "always",
        "analyze",
        "and",
        "as",
        "asc",
        "begin",
        "between",
        "by",
        "cascade",
        "case",
        "column",
        "commit",
        "create",
        "database",
        "delete",
        "desc",
        "distinct",
        "drop",
        "else",
        "end",
        "explain",
        "from",
        "group",
        "groups",
        "having",
        "in",
        "inner",
        "insert",
        "intersect",
        "into",
        "is",
        "join",
        "left",
        "like",
        "limit",
        "null",
        "on",
        "order",
        "outer",
        "over",
        "partition",
        "row",
        "savepoint",
        "select",
        "set",
        "table",
        "temp",
        "temporary",
        "then",
        "union",
        "update",
        "using",
        "values",
        "view",
        "when",
        "where",
        "window",
    ]

    FUNCTIONS = [
        ("array_select", "fn"),
        ("count", "agg"),
        ("greatest", "fn"),
        ("least", "fn"),
        ("list_select", "fn"),
        ("sqrt", "fn"),
        ("sum", "agg"),
    ]

    keyword_completions = [
        HarlequinCompletion(
            label=kw_name, type_label="kw", value=kw_name, priority=100, context=None
        )
        for kw_name in KEYWORDS
    ]

    function_completions = [
        HarlequinCompletion(
            label=label, type_label=type_label, value=label, priority=1000, context=None
        )
        for label, type_label in FUNCTIONS
    ]

    completions = [*keyword_completions, *function_completions]
    duckdb_completions = [
        (
            completion.label,
            completion.type_label,
            completion.priority,
            completion.context,
        )
        for completion in completions
    ]
    monkeypatch.setattr(
        "harlequin_sqlite.adapter.get_completion_data",
        lambda *_: completions,
        raising=True,
    )
    monkeypatch.setattr(
        "harlequin_duckdb.adapter.get_completion_data",
        lambda *_: duckdb_completions,
        raising=True,
    )


@pytest.fixture
def mock_pyperclip(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    mock.determine_clipboard.return_value = mock.copy, mock.paste

    def set_paste(x: str) -> None:
        mock.paste.return_value = x

    mock.copy.side_effect = set_paste
    monkeypatch.setattr("textual_textarea.text_editor.pyperclip", mock)

    return mock


@pytest.fixture
def wait_for_workers() -> Callable[[Harlequin], Awaitable[None]]:
    async def wait_for_filtered_workers(app: Harlequin) -> None:
        filtered_workers = [
            w for w in app.workers if w.name != "_database_tree_background_loader"
        ]
        if filtered_workers:
            with suppress(WorkerCancelled):
                await app.workers.wait_for_complete(filtered_workers)

    return wait_for_filtered_workers


@pytest.fixture
def expand_catalog_node() -> Callable[[Pilot, TreeNode[CatalogItem]], Awaitable[None]]:
    """Expand a catalog node and wait until its real children are rendered.

    The catalog shows a "loading…" placeholder child as soon as an unloaded node
    is expanded, so waiting on `node.children` alone returns before the adapter
    has answered.
    """

    async def expand(pilot: Pilot, node: TreeNode[CatalogItem]) -> None:
        node.expand()
        while True:
            data = node.data
            if data is None or (
                getattr(data, "loaded", True)
                and len(node.children) == len(data.children)
            ):
                return
            await pilot.pause()

    return expand


@pytest.fixture
def transaction_button_visible() -> Callable[[Harlequin], bool]:
    def fn(app: Harlequin) -> bool:
        """
        Skip snapshot checks for versions of that app showing the autocommit button.
        """
        return (
            sys.version_info >= (3, 12) and "Sqlite" in app.adapter.__class__.__name__
        )

    return fn
