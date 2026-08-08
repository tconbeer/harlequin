from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from textual.geometry import Offset
from textual.widgets._tabbed_content import ContentTab

from harlequin import Harlequin


@pytest.mark.asyncio
async def test_close_inactive_tab_via_x_does_not_crash(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Regression: clicking the ✕ on a tab that is NOT the active one used to
    crash with 'No Tab with id ...' because the click's tab-activation landed on
    the just-removed tab. Closing an inactive tab must remove exactly that tab."""
    async with app.run_test(size=(150, 40)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        ec = app.editor_collection
        await ec.action_new_buffer()
        await ec.action_new_buffer()
        await pilot.pause()
        assert ec.tab_count == 3
        active = ec.active
        assert active is not None

        # find a tab that is NOT active and click its ✕ (right edge of the tab)
        inactive_tab = next(
            c for c in ec.query(ContentTab) if c.id != ContentTab.add_prefix(active)
        )
        region = inactive_tab.region
        await pilot.click(
            offset=Offset(region.right - 2, region.y + region.height // 2)
        )
        # the close is fired on a short timer so the click's activation settles
        await pilot.pause(0.2)
        await pilot.pause(0.2)

        assert ec.tab_count == 2  # one closed, app still alive
