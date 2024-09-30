from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harlequin.catalog import InteractiveCatalogItem
from harlequin_sqlite.interactions import (
    execute_drop_table_statement,
    execute_drop_view_statement,
    insert_columns_at_cursor,
    show_describe_relation,
    show_relation_ddl,
    show_select_star,
)

if TYPE_CHECKING:
    from harlequin_sqlite.adapter import HarlequinSqliteConnection


@dataclass
class ColumnCatalogItem(InteractiveCatalogItem["HarlequinSqliteConnection"]):
    parent: "RelationCatalogItem" | None = None

    @classmethod
    def from_parent(
        cls,
        parent: "RelationCatalogItem",
        label: str,
        type_label: str,
    ) -> "ColumnCatalogItem":
        column_qualified_identifier = f'{parent.qualified_identifier}."{label}"'
        column_query_name = f'"{label}"'
        return cls(
            qualified_identifier=column_qualified_identifier,
            query_name=column_query_name,
            label=label,
            type_label=type_label,
            connection=parent.connection,
            parent=parent,
            loaded=True,
        )


@dataclass
class RelationCatalogItem(InteractiveCatalogItem["HarlequinSqliteConnection"]):
    INTERACTIONS = [
        ("Insert Columns at Cursor", insert_columns_at_cursor),
        ("Preview Data", show_select_star),
        ("Describe", show_describe_relation),
        ("Show DDL", show_relation_ddl),
    ]
    parent: "DatabaseCatalogItem" | None = None

    def fetch_children(self) -> list[ColumnCatalogItem]:
        if self.parent is None is None or self.connection is None:
            return []
        result = self.connection._get_columns(self.parent.label, self.label)
        return [
            ColumnCatalogItem.from_parent(
                parent=self,
                label=column_name,
                type_label=self.connection._short_column_type(column_type),
            )
            for _, column_name, column_type, *_ in result
        ]


class ViewCatalogItem(RelationCatalogItem):
    INTERACTIONS = RelationCatalogItem.INTERACTIONS + [
        ("Drop View", execute_drop_view_statement),
    ]

    @classmethod
    def from_parent(
        cls,
        parent: "DatabaseCatalogItem",
        label: str,
    ) -> "ViewCatalogItem":
        relation_query_name = f'"{parent.label}"."{label}"'
        relation_qualified_identifier = f'{parent.qualified_identifier}."{label}"'
        return cls(
            qualified_identifier=relation_qualified_identifier,
            query_name=relation_query_name,
            label=label,
            type_label="v",
            connection=parent.connection,
            parent=parent,
        )


class TableCatalogItem(RelationCatalogItem):
    INTERACTIONS = RelationCatalogItem.INTERACTIONS + [
        ("Drop Table", execute_drop_table_statement),
    ]

    @classmethod
    def from_parent(
        cls,
        parent: "DatabaseCatalogItem",
        label: str,
    ) -> "TableCatalogItem":
        relation_query_name = f'"{parent.label}"."{label}"'
        relation_qualified_identifier = f'{parent.qualified_identifier}."{label}"'
        return cls(
            qualified_identifier=relation_qualified_identifier,
            query_name=relation_query_name,
            label=label,
            type_label="t",
            connection=parent.connection,
            parent=parent,
        )


class DatabaseCatalogItem(InteractiveCatalogItem["HarlequinSqliteConnection"]):
    INTERACTIONS = []

    @classmethod
    def from_label(
        cls, label: str, connection: "HarlequinSqliteConnection"
    ) -> "DatabaseCatalogItem":
        database_identifier = f'"{label}"'
        return cls(
            qualified_identifier=database_identifier,
            query_name=database_identifier,
            label=label,
            type_label="db",
            connection=connection,
        )

    def fetch_children(self) -> list[RelationCatalogItem]:
        if self.connection is None:
            return []
        children: list[RelationCatalogItem] = []
        result = self.connection._get_relations(self.label)
        for table_label, table_type in result:
            if table_type == "view":
                children.append(
                    ViewCatalogItem.from_parent(
                        parent=self,
                        label=table_label,
                    )
                )
            else:
                children.append(
                    TableCatalogItem.from_parent(
                        parent=self,
                        label=table_label,
                    )
                )

        return children
