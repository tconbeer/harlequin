from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from textual.coordinate import Coordinate
from textual.pilot import Pilot
from textual.widgets import Input

from harlequin import Harlequin
from harlequin.components.cell_edit_modal import CellEditModal
from harlequin.components.results_viewer import ResultsTable


async def _run(
    app: Harlequin,
    pilot: Pilot,
    wait: Callable[[Harlequin], Awaitable[None]],
    sql: str,
) -> None:
    assert app.editor is not None
    app.editor.text = sql
    await pilot.press("ctrl+j")
    await wait(app)
    await pilot.pause()
    await wait(app)
    await pilot.pause()


@pytest.mark.asyncio
async def test_double_click_editable_cell_updates_value(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """End-to-end: editing a cell whose column maps to a real table column, with
    a primary key present, runs the UPDATE and reflects the new value."""
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()

        await _run(
            app, pilot, wait_for_workers, "create table t (id integer, name text)"
        )
        await _run(app, pilot, wait_for_workers, "insert into t values (1, 'a')")
        await _run(app, pilot, wait_for_workers, "select id, name from t")

        table = app.results_viewer.get_visible_table()
        assert table is not None
        # id (col 0) is the primary key; name (col 1) is editable
        table.edit_map = {
            0: ('"t"', '"id"', True),
            1: ('"t"', '"name"', False),
        }

        app.post_message(ResultsTable.CellEditRequested(row=0, column=1, value="a"))
        await pilot.pause()
        await pilot.pause()

        assert isinstance(app.screen, CellEditModal)
        app.screen.query_one("#cell-edit-input", Input).value = "b"
        await pilot.click("#cell-edit-run")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        # the displayed cell is updated only after the UPDATE succeeds
        assert table.get_cell_at(Coordinate(0, 1)) == "b"
        # and the change persisted to the database
        assert app.connection is not None
        cur = app.connection.execute("select name from t where id = 1")
        assert cur is not None
        rows = cur.fetchall()
        assert rows is not None
        assert rows.to_pylist() == [{"name": "b"}]


@pytest.mark.asyncio
async def test_edit_blocked_without_primary_key(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """If no primary-key column for the source table is in the result set, the
    row can't be identified, so no edit modal is opened."""
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        await _run(app, pilot, wait_for_workers, "select 1 as id, 'a' as name")

        table = app.results_viewer.get_visible_table()
        assert table is not None
        # editable column, but no primary key present
        table.edit_map = {1: ('"t"', '"name"', False)}

        app.post_message(ResultsTable.CellEditRequested(row=0, column=1, value="a"))
        await pilot.pause()
        await pilot.pause()

        assert not isinstance(app.screen, CellEditModal)


@pytest.mark.asyncio
async def test_edit_blocked_for_non_table_column(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A column that doesn't map to a plain table column (e.g. a computed value,
    or an adapter that doesn't report editability) can't be edited."""
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        await _run(app, pilot, wait_for_workers, "select 1 as a")

        table = app.results_viewer.get_visible_table()
        assert table is not None
        assert table.edit_map == {}  # duckdb doesn't report editable columns

        app.post_message(ResultsTable.CellEditRequested(row=0, column=0, value=1))
        await pilot.pause()
        await pilot.pause()

        assert not isinstance(app.screen, CellEditModal)
