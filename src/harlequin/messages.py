from typing import TYPE_CHECKING

from textual.message import Message

if TYPE_CHECKING:
    from textual.widget import Widget

    from harlequin.catalog import Catalog, CatalogItem


class WidgetMounted(Message):
    def __init__(self, widget: "Widget") -> None:
        super().__init__()
        self.widget = widget


class NewCatalog(Message):
    def __init__(self, catalog: "Catalog") -> None:
        self.catalog = catalog
        super().__init__()


class NewCatalogItems(Message):
    def __init__(self, parent: "CatalogItem", items: "list[CatalogItem]") -> None:
        self.parent = parent
        self.items = items
        super().__init__()
