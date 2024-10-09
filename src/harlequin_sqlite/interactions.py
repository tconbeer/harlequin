from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING, Literal, Sequence

from harlequin.catalog import CatalogItem
from harlequin.exception import HarlequinQueryError

if TYPE_CHECKING:
    from harlequin.driver import HarlequinDriver
    from harlequin_sqlite.catalog import (
        ColumnCatalogItem,
        RelationCatalogItem,
    )


def execute_drop_relation_statement(
    item: "RelationCatalogItem",
    driver: "HarlequinDriver",
    relation_type: Literal["view", "table"],
) -> None:
    def _drop_relation() -> None:
        if item.connection is None:
            return
        try:
            item.connection.execute(f"drop {relation_type} {item.qualified_identifier}")
        except HarlequinQueryError:
            driver.notify(
                f"Could not drop {relation_type} {item.label}", severity="error"
            )
            raise
        else:
            driver.notify(f"Dropped {relation_type} {item.label}")
            driver.refresh_catalog()

    driver.confirm_and_execute(callback=_drop_relation)


def execute_drop_table_statement(
    item: "RelationCatalogItem", driver: "HarlequinDriver"
) -> None:
    execute_drop_relation_statement(item=item, driver=driver, relation_type="table")


def execute_drop_view_statement(
    item: "RelationCatalogItem", driver: "HarlequinDriver"
) -> None:
    execute_drop_relation_statement(item=item, driver=driver, relation_type="view")


def show_describe_relation(
    item: "RelationCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    driver.insert_text_in_new_buffer(
        text=f"pragma table_info({item.qualified_identifier})"
    )


def show_relation_ddl(
    item: "RelationCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    if item.parent is None or item.connection is None:
        return
    cursor = item.connection.execute(
        f"""
        select sql
        from sqlite_schema
        where tbl_name = '{item.label}'
        limit 1
        """
    )
    assert cursor is not None
    result = cursor.fetchone()
    if result is not None:
        driver.insert_text_in_new_buffer(text=result[0])


def show_select_star(
    item: "RelationCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    driver.insert_text_in_new_buffer(
        dedent(
            f"""
            select *
            from {item.qualified_identifier}
            limit 100
            """.strip("\n")
        )
    )


def insert_columns_at_cursor(
    item: "RelationCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    if item.loaded:
        cols: Sequence["CatalogItem" | "ColumnCatalogItem"] = item.children
    else:
        cols = item.fetch_children()
    driver.insert_text_at_selection(text=",\n".join(c.query_name for c in cols))
