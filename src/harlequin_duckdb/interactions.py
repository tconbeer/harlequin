from __future__ import annotations

from typing import TYPE_CHECKING

from harlequin.catalog import InteractiveCatalogItem
from harlequin.exception import HarlequinQueryError

if TYPE_CHECKING:
    from harlequin.driver import HarlequinDriver


def execute_use_statement(
    item: "InteractiveCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    if item.connection is None:
        return
    try:
        item.connection.execute(f"use {item.qualified_identifier}")
    except HarlequinQueryError:
        driver.notify("Could not switch context", severity="error")
        raise
    else:
        driver.notify(f"Editor context switched to {item.label}")


def execute_drop_database_statement(
    item: "InteractiveCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    def _drop_database() -> None:
        if item.connection is None:
            return
        try:
            item.connection.execute(f"drop database {item.qualified_identifier}")
        except HarlequinQueryError:
            driver.notify("Could not drop database", severity="error")
            raise
        else:
            driver.notify(f"Dropped database {item.label}")

    if item.children or item.fetch_children():
        driver.confirm_and_execute(callback=_drop_database)
    else:
        _drop_database()
