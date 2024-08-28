from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harlequin.catalog import InteractiveCatalogItem
from harlequin_duckdb.interactions import (
    execute_drop_database_statement,
    execute_use_statement,
)

if TYPE_CHECKING:
    from harlequin_duckdb.adapter import DuckDbConnection


@dataclass
class ColumnCatalogItem(InteractiveCatalogItem["DuckDbConnection"]):
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
        )


@dataclass
class RelationCatalogItem(InteractiveCatalogItem["DuckDbConnection"]):
    parent: "SchemaCatalogItem" | None = None

    def fetch_children(self) -> list[ColumnCatalogItem]:
        if self.parent is None or self.parent.parent is None or self.connection is None:
            return []
        result = self.connection._get_columns(
            self.parent.parent.label, self.parent.label, self.label
        )
        return [
            ColumnCatalogItem.from_parent(
                parent=self,
                label=column_name,
                type_label=self.connection._short_column_type(column_type),
            )
            for column_name, column_type in result
        ]


class ViewCatalogItem(RelationCatalogItem):
    @classmethod
    def from_parent(
        cls,
        parent: "SchemaCatalogItem",
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
    @classmethod
    def from_parent(
        cls,
        parent: "SchemaCatalogItem",
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


class TempTableCatalogItem(TableCatalogItem):
    @classmethod
    def from_parent(
        cls,
        parent: "SchemaCatalogItem",
        label: str,
    ) -> "TempTableCatalogItem":
        relation_query_name = f'"{parent.label}"."{label}"'
        relation_qualified_identifier = f'{parent.qualified_identifier}."{label}"'
        return cls(
            qualified_identifier=relation_qualified_identifier,
            query_name=relation_query_name,
            label=label,
            type_label="tmp",
            connection=parent.connection,
            parent=parent,
        )


@dataclass
class SchemaCatalogItem(InteractiveCatalogItem["DuckDbConnection"]):
    parent: "DatabaseCatalogItem" | None = None

    @classmethod
    def from_parent(
        cls,
        parent: "DatabaseCatalogItem",
        label: str,
    ) -> "SchemaCatalogItem":
        schema_identifier = f'{parent.qualified_identifier}."{label}"'
        return cls(
            qualified_identifier=schema_identifier,
            query_name=schema_identifier,
            label=label,
            type_label="sch",
            connection=parent.connection,
            parent=parent,
        )

    def fetch_children(self) -> list[RelationCatalogItem]:
        if self.parent is None or self.connection is None:
            return []
        children: list[RelationCatalogItem] = []
        result = self.connection._get_tables(self.parent.label, self.label)
        for table_label, table_type in result:
            if table_type == "VIEW":
                children.append(
                    ViewCatalogItem.from_parent(
                        parent=self,
                        label=table_label,
                    )
                )
            elif table_type == "LOCAL TEMPORARY":
                children.append(
                    TempTableCatalogItem.from_parent(
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


class DatabaseCatalogItem(InteractiveCatalogItem["DuckDbConnection"]):
    INTERACTIONS = [
        ("Switch Editor Context (USE)", execute_use_statement),
        ("Drop Database", execute_drop_database_statement),
    ]

    @classmethod
    def from_label(
        cls, label: str, connection: "DuckDbConnection"
    ) -> "DatabaseCatalogItem":
        database_identifier = f'"{label}"'
        return cls(
            qualified_identifier=database_identifier,
            query_name=database_identifier,
            label=label,
            type_label="db",
            connection=connection,
        )

    def fetch_children(self) -> list[SchemaCatalogItem]:
        if self.connection is None:
            return []
        schemas = self.connection._get_schemas(self.label)
        return [
            SchemaCatalogItem.from_parent(
                parent=self,
                label=schema_label,
            )
            for (schema_label,) in schemas
        ]
