from pathlib import Path

import pytest
from textual.app import App


@pytest.fixture
def data_dir() -> Path:
    here = Path(__file__)
    return here.parent / "data"


class TestHelpers:
    @staticmethod
    async def await_data_loaded(app: App) -> None:
        # when the query is submitted, it should update app.query_text, app.relation,
        # and app.data using three different workers.
        await app.workers.wait_for_complete()
        await app.workers.wait_for_complete()
        await app.workers.wait_for_complete()


@pytest.fixture
def helpers() -> TestHelpers:
    return TestHelpers()
