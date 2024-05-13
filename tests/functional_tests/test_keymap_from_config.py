from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from harlequin import Harlequin, HarlequinAdapter
from harlequin.config import get_config_for_profile

QUERY = dedent(
    """
    select *
    from
        (
            values
                (1, 2, 3),
                (4, 5, 6),
                (7, 8, 9),
                (10, 11, 12),
                (13, 14, 15),
                (16, 17, 18),
                (19, 20, 21)
        ) foo(a, b, c)
"""
).strip()


@pytest.mark.asyncio
async def test_results_viewer_bindings(
    duckdb_adapter: type[HarlequinAdapter], data_dir: Path
) -> None:
    config_path = (
        data_dir / "functional_tests" / "test_keymap_from_config" / "config.toml"
    )
    profile, my_keymaps = get_config_for_profile(
        config_path=config_path, profile_name=None
    )
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        keymap_names=profile["keymap_name"],
        user_defined_keymaps=my_keymaps,
    )
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        while app.editor is None:
            await pilot.pause()

        q = QUERY
        app.editor.text = q
        await pilot.press("ctrl+j")

        while (table := app.results_viewer.get_visible_table()) is None:
            await pilot.pause()

        assert table is not None
        assert table.cursor_coordinate == (0, 0)
        assert table.selection_anchor_coordinate is None

        # simple navigation
        await pilot.press("s")
        assert table.cursor_coordinate == (1, 0)
        assert table.selection_anchor_coordinate is None
        await pilot.press("d")
        assert table.cursor_coordinate == (1, 1)
        assert table.selection_anchor_coordinate is None
        await pilot.press("d")
        assert table.cursor_coordinate == (1, 2)
        assert table.selection_anchor_coordinate is None
        await pilot.press("a")
        assert table.cursor_coordinate == (1, 1)
        assert table.selection_anchor_coordinate is None
        await pilot.press("w")
        assert table.cursor_coordinate == (0, 1)
        assert table.selection_anchor_coordinate is None
