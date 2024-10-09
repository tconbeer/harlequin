from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Generic, Protocol, Sequence, TypeVar

from textual.message import Message

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinConnection
    from harlequin.driver import HarlequinDriver


@dataclass
class CatalogItem:
    """
    A basic representation of a database object for Harlequin's Data Catalog.

    Args:
        qualified_identifier (str): The fully-scoped and (optionally) quoted database
            identifier for this object. Should be globally unique for the Connection.
            e.g., a postgres table: "mydb"."myschema"."mytable"
        query_name (str): The text to be inserted into the query editor for this
            object. This is typically the qualified_indentifier, but often
            for columns it is just the quoted name of the column (without
            the qualifying schema/table/etc).
        label (str): The unqualified name for this object, to appear in the Data
            Catalog widget.
        type_label (str): A short (1-3 chars) label denoting the type of this object
            or the data it contains. e.g., ("t") for "table" or "##" for
            "bigint column".
        children (list[CatalogItem]): Other nodes nested under this one (e.g.,
            a list of columns if this is a table.). If the list is empty and
            a CatalogItem subclass implements the `fetch_children()` method,
            Harlequin will attempt to call that method to lazy-load children.
    """

    qualified_identifier: str
    query_name: str
    label: str
    type_label: str
    children: list["CatalogItem"] = field(default_factory=list)


TCatalogItem_contra = TypeVar(
    "TCatalogItem_contra", bound=CatalogItem, contravariant=True
)
TAdapterConnection_contra = TypeVar(
    "TAdapterConnection_contra", bound="HarlequinConnection", contravariant=True
)


class Interaction(Protocol[TCatalogItem_contra]):
    def __call__(
        self,
        item: TCatalogItem_contra,
        driver: "HarlequinDriver",
    ) -> None: ...


@dataclass
class InteractiveCatalogItem(CatalogItem, Generic[TAdapterConnection_contra]):
    """
    An advanced representation of a database object that can lazy-load its
    children and provide other interactions via a context menu. Subclass
    this class and define the INTERACTIONS class variable to populate
    Harlequin's context menu.

    Each list item is a tuple with a label (for the context menu) and a
    callable that takes three arguments: the CatalogItem node that is clicked
    on, the initialized HarlequinConnection, and a HarlequinDriver that
    provides a simplified interface to the Harlequin App and allows the
    interactions to do things like insert text into the editor, show
    a confirmation modal, and display a notification (toast).
    """

    INTERACTIONS: ClassVar[Sequence[tuple[str, Interaction]] | None] = None
    loaded: bool = False
    """
    Harlequin will set loaded to True after calling fetch_children. You
    can prevent calls to fetch_children by initializing loaded=True.
    """
    connection: TAdapterConnection_contra | None = None

    def fetch_children(self) -> Sequence[CatalogItem]:
        """
        Returns a list of CatalogItems (or subclass instances, like other
        InteractiveCatalogItems) to be shown under this item in the catalog
        tree viewer. Return an empty list if this item has no children.
        """
        return []


@dataclass
class Catalog:
    """
    A representation of the queryable objects in a database, to be displayed in the
    DataCatalog widget.

    Args:
        items (list[CatalogItem]): A list of CatalogItem nodes, which each represent
            a database object, like a database, schema, table, or column.
    """

    items: list[CatalogItem]


class NewCatalog(Message):
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        super().__init__()


class NewCatalogItems(Message):
    def __init__(self, parent: CatalogItem, items: list[CatalogItem]) -> None:
        self.parent = parent
        self.items = items
        super().__init__()
