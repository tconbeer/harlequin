from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING, Literal, Sequence

from harlequin.catalog import CatalogItem
from harlequin.exception import HarlequinQueryError

if TYPE_CHECKING:
    from harlequin.driver import HarlequinDriver
    from harlequin_duckdb.catalog import (
        ColumnCatalogItem,
        DatabaseCatalogItem,
        RelationCatalogItem,
        SchemaCatalogItem,
    )


def execute_use_statement(
    item: "DatabaseCatalogItem" | "SchemaCatalogItem",
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


def execute_drop_schema_statement(
    item: "SchemaCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    def _drop_schema() -> None:
        if item.connection is None:
            return
        try:
            item.connection.execute(f"drop schema {item.qualified_identifier} cascade")
        except HarlequinQueryError:
            driver.notify(f"Could not drop schema {item.label}", severity="error")
            raise
        else:
            driver.notify(f"Dropped schema {item.label}")
            driver.refresh_catalog()

    if item.children or item.fetch_children():
        driver.confirm_and_execute(callback=_drop_schema)
    else:
        _drop_schema()


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


def show_export_database(
    item: "DatabaseCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    driver.insert_text_in_new_buffer(
        dedent(
            f"""
            use {item.qualified_identifier};
            export database './target_directory';
            """.strip("\n")
        )
    )


def show_describe_relation(
    item: "RelationCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    driver.insert_text_in_new_buffer(text=f"describe {item.qualified_identifier}")


def show_summarize_relation(
    item: "RelationCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    driver.insert_text_in_new_buffer(text=f"summarize {item.qualified_identifier}")


def show_relation_ddl(
    item: "RelationCatalogItem",
    driver: "HarlequinDriver",
    relation_type: Literal["view", "table"],
) -> None:
    if item.parent is None or item.parent.parent is None or item.connection is None:
        return
    table_function = "duckdb_tables()" if relation_type == "table" else "duckdb_views()"
    cursor = item.connection.execute(
        f"""
        select sql
        from {table_function}
        where database_name = '{item.parent.parent.label}'
        and schema_name = '{item.parent.label}'
        and {relation_type}_name = '{item.label}'
        limit 1
        """
    )
    assert cursor is not None
    result = cursor.fetchone()
    if result is not None:
        driver.insert_text_in_new_buffer(text=result[0])


def show_table_ddl(
    item: "RelationCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    show_relation_ddl(item=item, driver=driver, relation_type="table")


def show_view_ddl(
    item: "RelationCatalogItem",
    driver: "HarlequinDriver",
) -> None:
    show_relation_ddl(item=item, driver=driver, relation_type="view")


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
