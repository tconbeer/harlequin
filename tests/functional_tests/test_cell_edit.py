from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from textual.coordinate import Coordinate
from textual.pilot import Pilot
from textual.widgets import Input

from harlequin import Harlequin
from harlequin.components.cell_edit_modal import CellEditModal

TABLE_COLUMNS = {0: ('"t"', '"id"', True), 1: ('"t"', '"name"', False)}


def report_editable_columns(
    monkeypatch: pytest.MonkeyPatch, columns: dict[int, tuple[str, str, bool]]
) -> None:
    """DuckDB can't say where a result column comes from; pretend it can."""
    monkeypatch.setattr(
        "harlequin_duckdb.adapter.DuckDbCursor.editable_columns", lambda self: columns
    )


async def run_query(
    app: Harlequin,
    pilot: Pilot[None],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    query: str,
) -> None:
    assert app.editor is not None
    app.editor.text = query
    await pilot.press("ctrl+j")
    await wait_for_workers(app)
    await pilot.pause()
    await wait_for_workers(app)
    await pilot.pause()


def stored_names(app: Harlequin) -> list[str]:
    assert app.connection is not None
    cursor = app.connection.execute("select name from t order by id")
    assert cursor is not None
    data = cursor.fetchall()
    assert data is not None
    return [row["name"] for row in data.to_pylist()]


@pytest.mark.asyncio
async def test_edit_cell(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_editable_columns(monkeypatch, TABLE_COLUMNS)
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        await run_query(
            app, pilot, wait_for_workers, "create table t (id integer, name text)"
        )
        await run_query(app, pilot, wait_for_workers, "insert into t values (1, 'a')")
        await run_query(app, pilot, wait_for_workers, "select id, name from t")

        table = app.results_viewer.get_visible_table()
        assert table is not None
        assert table.editable_columns == TABLE_COLUMNS
        assert app.results_viewer._has_focus_within
        table.move_cursor(row=0, column=1)

        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, CellEditModal)
        assert app.screen.query_one(Input).value == "a"
        assert app.screen.statement == 'update "t"\nset "name" = …\nwhere "id" = 1'
        assert await app_snapshot(app, "edit cell modal")

        # escape closes without running anything
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CellEditModal)
        assert stored_names(app) == ["a"]

        # a double-click on the cell opens the same editor
        cell = table._get_cell_region(Coordinate(0, 1))
        await pilot.double_click(table, offset=(cell.x + 1, cell.y))
        await pilot.pause()
        assert isinstance(app.screen, CellEditModal)
        assert app.screen.query_one(Input).value == "a"
        await pilot.press("escape")
        await pilot.pause()

        # enter runs the update; the grid shows what the database now holds
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, CellEditModal)
        app.screen.query_one(Input).value = "b"
        await pilot.press("enter")
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        assert not isinstance(app.screen, CellEditModal)
        assert stored_names(app) == ["b"]
        assert table.get_cell_at(Coordinate(0, 1)) == "b"
        assert list(app._notifications)[-1].message == "Cell updated."


@pytest.mark.asyncio
async def test_edit_cell_needs_the_primary_key(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_editable_columns(monkeypatch, {0: ('"t"', '"name"', False)})
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        await run_query(
            app, pilot, wait_for_workers, "create table t (id integer, name text)"
        )
        await run_query(app, pilot, wait_for_workers, "insert into t values (1, 'a')")
        await run_query(app, pilot, wait_for_workers, "select name from t")

        await pilot.press("e")
        await pilot.pause()
        assert not isinstance(app.screen, CellEditModal)
        assert "primary key" in list(app._notifications)[-1].message
        assert stored_names(app) == ["a"]


@pytest.mark.asyncio
async def test_edit_cell_needs_a_table_column(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """DuckDB reports no editable columns, so nothing in its results can be edited."""
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        await run_query(app, pilot, wait_for_workers, "select 1 as x")

        table = app.results_viewer.get_visible_table()
        assert table is not None
        assert table.editable_columns == {}
        await pilot.press("e")
        await pilot.pause()
        assert not isinstance(app.screen, CellEditModal)
        assert "column" in list(app._notifications)[-1].message
