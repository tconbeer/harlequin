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


class ColumnCatalogItem(InteractiveCatalogItem):
    @classmethod
    def from_label(
        cls,
        label: str,
        type_label: str,
        relation_label: str,
        schema_label: str,
        database_label: str,
    ) -> "ColumnCatalogItem":
        column_query_name = f'"{label}"'
        return cls(
            qualified_identifier=(
                f'"{database_label}"."{schema_label}"."{relation_label}"."{label}"'
            ),
            query_name=column_query_name,
            label=label,
            type_label=type_label,
        )


@dataclass
class RelationCatalogItem(InteractiveCatalogItem):
    database_label: str = ""
    schema_label: str = ""

    def fetch_children(self, connection: "DuckDbConnection") -> list[ColumnCatalogItem]:
        result = connection._get_columns(
            self.database_label, self.schema_label, self.label
        )
        return [
            ColumnCatalogItem.from_label(
                column_name,
                connection._short_column_type(column_type),
                self.label,
                self.schema_label,
                self.database_label,
            )
            for column_name, column_type in result
        ]


class ViewCatalogItem(RelationCatalogItem):
    @classmethod
    def from_label(
        cls, label: str, schema_label: str, database_label: str
    ) -> "ViewCatalogItem":
        relation_query_name = f'"{schema_label}"."{label}"'
        relation_qualified_identifier = f'"{database_label}".{relation_query_name}'
        return cls(
            qualified_identifier=relation_qualified_identifier,
            query_name=relation_query_name,
            label=label,
            type_label="v",
            database_label=database_label,
            schema_label=schema_label,
        )


class TableCatalogItem(RelationCatalogItem):
    @classmethod
    def from_label(
        cls, label: str, schema_label: str, database_label: str
    ) -> "TableCatalogItem":
        relation_query_name = f'"{schema_label}"."{label}"'
        relation_qualified_identifier = f'"{database_label}".{relation_query_name}'
        return cls(
            qualified_identifier=relation_qualified_identifier,
            query_name=relation_query_name,
            label=label,
            type_label="t",
            database_label=database_label,
            schema_label=schema_label,
        )


class TempTableCatalogItem(TableCatalogItem):
    @classmethod
    def from_label(
        cls, label: str, schema_label: str, database_label: str
    ) -> "TempTableCatalogItem":
        relation_query_name = f'"{schema_label}"."{label}"'
        relation_qualified_identifier = f'"{database_label}".{relation_query_name}'
        return cls(
            qualified_identifier=relation_qualified_identifier,
            query_name=relation_query_name,
            label=label,
            type_label="tmp",
            database_label=database_label,
            schema_label=schema_label,
        )


@dataclass
class SchemaCatalogItem(InteractiveCatalogItem):
    database_label: str = ""

    @classmethod
    def from_label(cls, label: str, database_label: str) -> "SchemaCatalogItem":
        schema_identifier = f'"{database_label}"."{label}"'
        return cls(
            qualified_identifier=schema_identifier,
            query_name=schema_identifier,
            label=label,
            type_label="sch",
            database_label=database_label,
        )

    def fetch_children(
        self, connection: "DuckDbConnection"
    ) -> list[RelationCatalogItem]:
        children: list[RelationCatalogItem] = []
        result = connection._get_tables(self.database_label, self.label)
        for table_label, table_type in result:
            if table_type == "VIEW":
                children.append(
                    ViewCatalogItem.from_label(
                        table_label, self.label, self.database_label
                    )
                )
            elif table_type == "LOCAL TEMPORARY":
                children.append(
                    TempTableCatalogItem.from_label(
                        table_label, self.label, self.database_label
                    )
                )
            else:
                children.append(
                    TableCatalogItem.from_label(
                        table_label, self.label, self.database_label
                    )
                )

        return children


class DatabaseCatalogItem(InteractiveCatalogItem["DuckDbConnection"]):
    INTERACTIONS = [
        ("Switch Editor Context (USE)", execute_use_statement),
        ("Drop Database", execute_drop_database_statement),
    ]

    @classmethod
    def from_label(cls, label: str) -> "DatabaseCatalogItem":
        database_identifier = f'"{label}"'
        return cls(
            qualified_identifier=database_identifier,
            query_name=database_identifier,
            label=label,
            type_label="db",
        )

    def fetch_children(self, connection: "DuckDbConnection") -> list[SchemaCatalogItem]:
        schemas = connection._get_schemas(self.label)
        return [
            SchemaCatalogItem.from_label(schema_label, self.qualified_identifier)
            for (schema_label,) in schemas
        ]
