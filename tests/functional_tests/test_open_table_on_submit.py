from __future__ import annotations

from typing import Awaitable, Callable

import pytest

from harlequin import Harlequin
from harlequin.app import QuerySubmitted
from harlequin.components.data_catalog.tree import HarlequinTree


class _RelationCatalogItem:
    """Stand-in whose class name matches the adapters' relation item, which is
    how the app recognizes a table/view node (checked by name to avoid importing
    an adapter). A column/schema node would NOT have this name."""

    def __init__(self, query_name: str) -> None:
        self.query_name = query_name


# match the real class name the app looks for
_RelationCatalogItem.__name__ = "RelationCatalogItem"


class _FakeNode:
    def __init__(self, data: object) -> None:
        self.data = data


@pytest.mark.asyncio
async def test_submitting_a_table_runs_select_star(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Double-clicking / submitting a relation node opens and runs
    'select * from <relation> limit 100' instead of inserting the name."""
    messages: list = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        node = _FakeNode(_RelationCatalogItem('"main"."foo"'))
        app.post_message(HarlequinTree.NodeSubmitted(node=node))  # type: ignore[arg-type]
        await pilot.pause()
        await pilot.pause()

        submitted = [m for m in messages if isinstance(m, QuerySubmitted)]
        assert submitted, "submitting a table should run a query"
        assert any(
            "select *" in q.lower() and "limit 100" in q.lower()
            for m in submitted
            for q in m.queries
        )


@pytest.mark.asyncio
async def test_submitting_a_non_relation_inserts_name(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A non-relation node (e.g. a column) keeps the old behavior: its name is
    inserted into the editor and no query is run."""
    messages: list = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = ""

        class _Column:  # not named RelationCatalogItem
            query_name = "some_col"

        node = _FakeNode(_Column())
        app.post_message(HarlequinTree.NodeSubmitted(node=node))  # type: ignore[arg-type]
        await pilot.pause()
        await pilot.pause()

        # a non-relation node must NOT run a query (it inserts text instead)
        assert not [m for m in messages if isinstance(m, QuerySubmitted)]
        assert app.editor.text != ""  # something was inserted into the editor
