from dataclasses import dataclass, field
from typing import List

from textual.message import Message


@dataclass
class CatalogItem:
    """
    A representation of a database object.

    Args:
        qualified_identifier (str): The fully-scoped and (optionally) quoted database
            identifier for this object. Should be globally unique for the Connection.
            e.g., a postgres table: "mydb"."myschema"."mytable"
        query_name (str): The text to be inserted into the query editor for this
            object.
        label (str): The unqualified name for this object, to appear in the Data
            Catalog widget.
        type_label (str): A short (1-3 chars) label denoting the type of this object
            or the data it contains. e.g., ("t") for "table" or "##" for
            "bigint column".
        children (list[CatalogItem]): Other nodes nested under this one (e.g.,
            a list of columns if this is a table.)
    """

    qualified_identifier: str
    query_name: str
    label: str
    type_label: str
    children: List["CatalogItem"] = field(default_factory=list)


@dataclass
class Catalog:
    """
    A representation of the queryable objects in a database, to be displayed in the
    DataCatalog widget.

    Args:
        items (list[CatalogItem]): A list of CatalogItem nodes, which each represent
            a database object, like a database, schema, table, or column.
    """

    items: List[CatalogItem]


class NewCatalog(Message):
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        super().__init__()
