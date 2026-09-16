"""The waits that know what Harlequin's widgets look like, over `tests.waiting`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harlequin.app import Harlequin
from harlequin.catalog import Catalog, CatalogItem
from harlequin.components import ErrorModal
from harlequin.components.code_editor import CodeEditor
from harlequin.components.data_catalog.database_tree import DatabaseTree
from harlequin.components.results_viewer import ResultsTable
from tests.waiting import wait_for, wait_for_value

if TYPE_CHECKING:
    from textual.pilot import Pilot
    from textual.widgets._tree import TreeNode


async def wait_for_editor(pilot: Pilot, app: Harlequin) -> CodeEditor:
    """The Query Editor, once the app has one.

    The editor collection is mounted lazily, so an app that is running does not
    yet have an editor to type into.
    """
    return await wait_for_value(
        pilot, lambda: app.editor, description="the Query Editor to be mounted"
    )


async def wait_for_any_table(pilot: Pilot, app: Harlequin) -> ResultsTable:
    """The table the Results Viewer is showing, once a query has filled one in.

    Any table in the active pane: a re-run clears the panes without awaiting
    the removal, so this can still hand back the previous query's table. A test
    that runs a second query gates on `results_viewer.tab_count` instead.
    """
    return await wait_for_value(
        pilot,
        app.results_viewer.get_visible_table,
        description="the Results Viewer to show a table",
    )


async def wait_for_error_modal(pilot: Pilot, app: Harlequin) -> ErrorModal:
    """The error modal on top of the stack, once the app has raised one."""
    return await wait_for_value(
        pilot,
        lambda: app.screen if isinstance(app.screen, ErrorModal) else None,
        description="an error modal",
    )


async def wait_for_catalog_tree(pilot: Pilot, app: Harlequin) -> DatabaseTree:
    """The Data Catalog's database tree, once it has rendered what it found.

    The tree's own background loader is not one of the workers
    `wait_for_workers` waits on, so an app whose workers are all done can still
    be showing an empty catalog.
    """
    tree = app.data_catalog.database_tree
    await wait_for(
        pilot,
        lambda: not tree.loading and bool(tree.root.children),
        description="the Data Catalog to render its databases",
    )
    return tree


async def rendered_catalog(pilot: Pilot, app: Harlequin) -> Catalog:
    """The catalog the tree is holding, once its nodes are mounted.

    `wait_for_workers` returns when `get_catalog()` has answered, which is
    before the app has handled the message carrying its result, so reading the
    tree straight after it races the message pump. `loading` is what the tree
    clears at the end of `watch_catalog`, after the reload that mounts the
    nodes -- setting `catalog` alone says nothing is on screen yet.
    """
    tree = app.data_catalog.database_tree
    return await wait_for_value(
        pilot,
        lambda: tree.catalog if not tree.loading else None,
        description="the data catalog to load",
    )


async def replaced_catalog(pilot: Pilot, app: Harlequin, previous: Catalog) -> Catalog:
    """The catalog the tree is holding once it is no longer `previous`."""
    tree = app.data_catalog.database_tree
    return await wait_for_value(
        pilot,
        lambda: (
            tree.catalog if tree.catalog is not previous and not tree.loading else None
        ),
        description="the data catalog to be replaced",
    )


async def first_database_node(pilot: Pilot, app: Harlequin) -> TreeNode[CatalogItem]:
    """The tree's first database node, once the catalog has been rendered."""
    tree = await wait_for_catalog_tree(pilot, app)
    return tree.root.children[0]


async def expand_catalog_node(pilot: Pilot, node: TreeNode[CatalogItem]) -> None:
    """Expand a catalog node and wait until its real children are rendered.

    The catalog shows a "loading…" placeholder child as soon as an unloaded
    node is expanded, and `fetch_children` sets `loaded` on the worker thread
    while the node is repopulated later on the event loop -- so a node with one
    real child passes a count comparison while the placeholder is still what is
    on screen.
    """
    node.expand()

    def children_are_rendered() -> bool:
        data = node.data
        if data is None:
            return True
        return (
            getattr(data, "loaded", True)
            and not DatabaseTree._has_placeholder(node)
            and len(node.children) == len(data.children)
        )

    await wait_for(
        pilot,
        children_are_rendered,
        description=f"the children of {node.label!s} to be rendered",
    )
