"""The path grammar, and the walk it drives.

Two things are asserted here that nothing else can: that a path spelled by a
listing is a path `parse()` reads back as the same segments -- which is what
lets an agent walk a catalog by copying a cell out of the last answer -- and
that the walk costs one `fetch_children()` per level and no more.

The catalog is hand-built rather than a database's: what varies between adapters
is how deep it goes and what a level is called, and a fake is the only way to
assert against a shape no bundled adapter has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Sequence

import pytest

from harlequin.adapter import HarlequinConnection, HarlequinCursor
from harlequin.catalog import Catalog, CatalogItem, InteractiveCatalogItem
from harlequin.exception import HarlequinCatalogPathError
from harlequin.navigate import CatalogPath, list_children, resolve, spell

FETCHES: list[str] = []
"""Every `fetch_children()` the walk made, in order. One entry is one round trip."""


@dataclass
class FakeItem(InteractiveCatalogItem["FakeConnection"]):
    """A node that records the times it was asked to fetch."""

    to_fetch: list["FakeItem"] = field(default_factory=list)

    def fetch_children(self) -> Sequence[CatalogItem]:
        FETCHES.append(self.label)
        return self.to_fetch


def item(label: str, *children: FakeItem, type_label: str = "t") -> FakeItem:
    return FakeItem(
        qualified_identifier=label,
        query_name=f'"{label}"',
        label=label,
        type_label=type_label,
        to_fetch=list(children),
    )


class FakeConnection(HarlequinConnection):
    """Just enough connection for the walk: a catalog, and a count of asks."""

    def __init__(self, *items: CatalogItem) -> None:
        super().__init__()
        self.items = list(items)
        self.catalog_calls = 0

    def execute(self, query: str) -> HarlequinCursor | None:
        raise AssertionError("the walk ran SQL")

    def get_catalog(self) -> Catalog:
        self.catalog_calls += 1
        return Catalog(items=list(self.items))


@pytest.fixture(autouse=True)
def fetches() -> Iterator[list[str]]:
    FETCHES.clear()
    yield FETCHES
    FETCHES.clear()


@pytest.fixture
def connection() -> FakeConnection:
    return FakeConnection(
        item(
            "mydb",
            item(
                "analytics",
                item("orders", item("id", type_label="##")),
                item("order_items"),
                type_label="sch",
            ),
            item("empty", type_label="sch"),
            type_label="db",
        )
    )


# --- the grammar -------------------------------------------------------------


@pytest.mark.parametrize(
    "text,segments",
    [
        (None, ()),
        ("", ()),
        ("   ", ()),
        ("mydb", ("mydb",)),
        ("mydb.analytics", ("mydb", "analytics")),
        ('mydb."my.schema"', ("mydb", "my.schema")),
        ('"quoted ""name"""', ('quoted "name"',)),
        ('mydb."has*star"', ("mydb", "has*star")),
        ('""', ("",)),
    ],
)
def test_a_path_is_dotted_segments(text: str | None, segments: tuple[str, ...]) -> None:
    parsed = CatalogPath.parse(text)
    assert parsed.segments == segments
    assert parsed.glob is None


@pytest.mark.parametrize("text", ["mydb.ord*", "ord*", "mydb.analytics.?d"])
def test_a_trailing_wildcard_is_a_filter_not_a_segment(text: str) -> None:
    parsed = CatalogPath.parse(text)
    assert parsed.glob == text.rsplit(".", 1)[-1]
    assert parsed.segments == tuple(text.split(".")[:-1])


@pytest.mark.parametrize("text", ["*.orders", "mydb.*.orders", "my?b.analytics"])
def test_an_interior_wildcard_is_refused(text: str) -> None:
    """It cannot be answered without fetching every level it could match, so it
    is refused rather than quietly walked."""
    with pytest.raises(HarlequinCatalogPathError, match="wildcard"):
        CatalogPath.parse(text)


@pytest.mark.parametrize("text", ["mydb.", ".mydb", "mydb..analytics"])
def test_an_empty_segment_is_refused(text: str) -> None:
    with pytest.raises(HarlequinCatalogPathError, match="empty path segment"):
        CatalogPath.parse(text)


def test_an_unterminated_quote_is_refused() -> None:
    with pytest.raises(HarlequinCatalogPathError, match="never closes"):
        CatalogPath.parse('mydb."analytics')


@pytest.mark.parametrize(
    "segments",
    [
        ("mydb", "analytics"),
        ("my.db", "analytics"),
        ('a "quoted" one', "b"),
        ("has*star",),
        ("",),
    ],
)
def test_a_spelled_path_parses_back_to_the_same_segments(
    segments: tuple[str, ...],
) -> None:
    """The property the `path` column depends on: a cell from one listing is
    the argument that lists that item's children."""
    assert CatalogPath.parse(spell(segments)).segments == segments


def test_a_plain_segment_is_spelled_without_quotes() -> None:
    """A path a person reads should look like the one they would have typed."""
    assert spell(["mydb", "analytics", "orders"]) == "mydb.analytics.orders"


# --- the walk ----------------------------------------------------------------


def test_the_top_level_is_the_catalog(connection: FakeConnection) -> None:
    listing = list_children(connection, CatalogPath.parse(None))
    assert listing.parent is None
    assert [child.label for child in listing.items] == ["mydb"]
    assert connection.catalog_calls == 1


def test_a_listing_is_one_round_trip_per_segment_plus_one(
    connection: FakeConnection,
) -> None:
    """The cost this module promises, and the reason there is no `--depth`."""
    listing = list_children(connection, CatalogPath.parse("mydb.analytics"))
    assert listing.parent is not None
    assert listing.parent.label == "analytics"
    assert [child.label for child in listing.items] == ["orders", "order_items"]

    # one for the catalog, and one per segment walked past -- and nothing else
    assert connection.catalog_calls == 1
    assert FETCHES == ["mydb", "analytics"]


def test_listing_a_relation_is_describing_it(connection: FakeConnection) -> None:
    listing = list_children(connection, CatalogPath.parse("mydb.analytics.orders"))
    assert [(child.label, child.type_label) for child in listing.items] == [
        ("id", "##")
    ]


def test_a_node_with_no_children_lists_nothing(connection: FakeConnection) -> None:
    """Zero rows, not an error: an empty schema is an answer."""
    assert list_children(connection, CatalogPath.parse("mydb.empty")).items == []


def test_a_trailing_wildcard_filters_one_level(connection: FakeConnection) -> None:
    listing = list_children(connection, CatalogPath.parse("mydb.analytics.order*"))
    assert [child.label for child in listing.items] == ["orders", "order_items"]
    # the parent it filtered, not a level below it
    assert listing.parent is not None
    assert listing.parent.label == "analytics"


def test_a_wildcard_that_matches_nothing_lists_nothing(
    connection: FakeConnection,
) -> None:
    assert list_children(connection, CatalogPath.parse("mydb.z*")).items == []


def test_a_segment_that_names_nothing_says_what_is_there(
    connection: FakeConnection,
) -> None:
    """A guessed path is the common case, so the error answers the question."""
    with pytest.raises(HarlequinCatalogPathError) as excinfo:
        list_children(connection, CatalogPath.parse("mydb.analytic"))
    assert "analytic" in excinfo.value.msg
    assert "Did you mean analytics?" in excinfo.value.msg


def test_a_top_level_miss_says_where_it_looked(connection: FakeConnection) -> None:
    with pytest.raises(HarlequinCatalogPathError, match="top of the catalog"):
        list_children(connection, CatalogPath.parse("nope"))


def test_a_miss_under_a_childless_node_says_so(connection: FakeConnection) -> None:
    with pytest.raises(HarlequinCatalogPathError, match="nothing under it"):
        list_children(connection, CatalogPath.parse("mydb.empty.orders"))


def test_resolve_fetches_the_ancestors_and_not_the_level(
    connection: FakeConnection,
) -> None:
    """`resolve()` is the walk without the listing, and costs one call less."""
    found = resolve(connection, CatalogPath.parse("mydb.analytics"))
    assert found is not None and found.label == "analytics"
    assert FETCHES == ["mydb"]


def test_children_already_loaded_are_not_fetched_again(
    connection: FakeConnection,
) -> None:
    """An adapter that hands over a whole subtree is not asked to do it twice."""
    path = CatalogPath.parse("mydb.analytics")
    list_children(connection, path)
    list_children(connection, path)
    assert FETCHES == ["mydb", "analytics"]


def test_a_plain_catalog_item_has_no_children_to_fetch() -> None:
    """The degradation path: an adapter whose items are not interactive."""
    connection = FakeConnection(
        CatalogItem(
            qualified_identifier="db", query_name='"db"', label="db", type_label="db"
        )
    )
    assert list_children(connection, CatalogPath.parse("db")).items == []
