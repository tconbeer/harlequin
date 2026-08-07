from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from rich.style import Style
from rich.text import Text
from textual import on
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import (
    ContentSwitcher,
    TabbedContent,
    TabPane,
    Tabs,
)
from textual_fastdatatable import DataTable
from textual_fastdatatable.backend import AutoBackendType

from harlequin.messages import WidgetMounted

if TYPE_CHECKING:
    from textual_fastdatatable.backend import DataTableBackend
    from textual_fastdatatable.data_table import CursorType


SORT_ASC_GLYPH = "▲"
SORT_DESC_GLYPH = "▼"
SORT_HINT_GLYPH = "⇅"
"""Shown on every unsorted column, so the header width doesn't shift on sort."""


class ResultsTable(DataTable, inherit_bindings=False):
    DEFAULT_CSS = """
        ResultsTable {
            height: 100%;
            width: 100%;
        }
    """

    class SortRequested(Message):
        """Posted when the user clicks a column header to sort by that column."""

        def __init__(self, column: str, descending: bool) -> None:
            self.column = column
            self.descending = descending
            super().__init__()

    def on_mount(self) -> None:
        self.post_message(WidgetMounted(widget=self))

    @on(DataTable.HeaderSelected)
    def _sort_on_header_click(self, event: DataTable.HeaderSelected) -> None:
        """Clicking a header cycles that column asc -> desc -> asc."""
        event.stop()
        index = event.column_index
        if not 0 <= index < len(self.plain_column_labels):
            return
        column = self.plain_column_labels[index]
        # A column we aren't sorted by starts ascending; the current one flips.
        descending = column == self.sort_column and not self.sort_descending
        self.post_message(self.SortRequested(column=column, descending=descending))

    def __init__(
        self,
        *,
        backend: "DataTableBackend" | None = None,
        data: Any | None = None,
        column_labels: list[str | Text] | None = None,
        plain_column_labels: list[str | Text] | None = None,
        source_query: str = "",
        sort_column: str | None = None,
        sort_descending: bool = False,
        column_widths: list[int | None] | None = None,
        max_column_content_width: int | None = None,
        show_header: bool = True,
        show_row_labels: bool = True,
        max_rows: int | None = None,
        fixed_rows: int = 0,
        fixed_columns: int = 0,
        zebra_stripes: bool = False,
        header_height: int = 1,
        show_cursor: bool = True,
        cursor_foreground_priority: Literal["renderable", "css"] = "css",
        cursor_background_priority: Literal["renderable", "css"] = "renderable",
        cursor_type: "CursorType" = "cell",
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
        null_rep: str = "",
        render_markup: bool = True,
    ):
        self.plain_column_labels: list[str] = (
            [str(label) for label in plain_column_labels]
            if plain_column_labels is not None
            else []
        )
        self.source_query = source_query
        """The SQL that produced this result, and that sorting rewrites."""
        self.sort_column = sort_column
        self.sort_descending = sort_descending
        super().__init__(
            backend=backend,
            data=data,
            column_labels=column_labels,
            column_widths=column_widths,
            max_column_content_width=max_column_content_width,
            show_header=show_header,
            show_row_labels=show_row_labels,
            max_rows=max_rows,
            fixed_rows=fixed_rows,
            fixed_columns=fixed_columns,
            zebra_stripes=zebra_stripes,
            header_height=header_height,
            show_cursor=show_cursor,
            cursor_foreground_priority=cursor_foreground_priority,
            cursor_background_priority=cursor_background_priority,
            cursor_type=cursor_type,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            null_rep=null_rep,
            render_markup=render_markup,
        )


class ResultsViewer(TabbedContent, can_focus=True):
    BORDER_TITLE = "Query Results"
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "results-viewer--type-label",
    }

    def __init__(
        self,
        max_results: int = 10_000,
    ) -> None:
        super().__init__()
        self.max_results = max_results

    def on_mount(self) -> None:
        self.query_one(Tabs).can_focus = False
        self.add_class("hide-tabs")
        self.max_col_width = self._get_max_col_width()
        self.post_message(WidgetMounted(widget=self))

    def clear_all_tables(self) -> None:
        self.clear_panes()
        self.add_class("hide-tabs")

    def get_visible_table(self) -> ResultsTable | None:
        content = self.query_one(ContentSwitcher)
        active_tab_id = self.active
        if active_tab_id:
            try:
                tab_pane = content.query_one(f"#{active_tab_id}", TabPane)
                return tab_pane.query_one(ResultsTable)
            except NoMatches:
                return None
        else:
            tables = content.query(ResultsTable)
            try:
                return tables.first(ResultsTable)
            except NoMatches:
                return None

    async def push_table(
        self,
        table_id: str,
        column_labels: list[tuple[str, str]],
        data: AutoBackendType,
        source_query: str = "",
        sort_column: str | None = None,
        sort_descending: bool = False,
    ) -> ResultsTable:
        formatted_labels = [
            self._format_column_label(
                col_name,
                col_type,
                sorted_descending=sort_descending if col_name == sort_column else None,
            )
            for col_name, col_type in column_labels
        ]
        table = ResultsTable(
            id=table_id,
            column_labels=formatted_labels,  # type: ignore
            plain_column_labels=[col_name for (col_name, _) in column_labels],
            source_query=source_query,
            sort_column=sort_column,
            sort_descending=sort_descending,
            data=data,
            max_rows=self.max_results,
            cursor_type="range",
            max_column_content_width=self.max_col_width,
            null_rep="[dim]∅ null[/]",
            render_markup=False,
        )
        n = self.tab_count + 1
        if n > 1:
            self.remove_class("hide-tabs")
        pane = TabPane(f"Result {n}", table, id=f"result-{n}")
        await self.add_pane(pane)
        # need to manually refresh the table, since activating the tab
        # doesn't consistently cause a new layout calc.
        table.refresh(repaint=True, layout=True)
        return table

    def show_loading(self) -> None:
        self.border_title = "Running Query"
        self.add_class("non-responsive")
        self.loading = True
        self.clear_all_tables()

    def show_table(self, did_run: bool = True) -> None:
        self.loading = False
        self.remove_class("non-responsive")
        if not did_run:
            self.border_title = "Query Results"
        else:
            table = self.get_visible_table()
            if table is not None:
                rows = table.source_row_count
                if rows > 0:
                    self.border_title = (
                        f"Query Results {self._human_row_count(table.source_row_count)}"
                    )
                else:
                    self.border_title = "Query Returned No Records"
            else:
                self.border_title = "Query Results"

    def on_focus(self) -> None:
        self._focus_on_visible_table()

    def on_resize(self) -> None:
        # only impacts new tables pushed after the resize
        self.max_col_width = self._get_max_col_width()

    def on_tabbed_content_tab_activated(
        self, message: TabbedContent.TabActivated
    ) -> None:
        message.stop()
        maybe_table = self.get_visible_table()
        if maybe_table is not None:
            self.border_title = (
                f"Query Results {self._human_row_count(maybe_table.source_row_count)}"
            )
            maybe_table.focus()

    def action_switch_tab(self, offset: int) -> None:
        if not self.active:
            return
        name_prefix, _, tab_number_str = self.active.rpartition("-")
        tab_number = int(tab_number_str)
        unsafe_tab_number = tab_number + offset
        if unsafe_tab_number < 1:
            new_tab_number = self.tab_count
        elif unsafe_tab_number > self.tab_count:
            new_tab_number = 1
        else:
            new_tab_number = unsafe_tab_number
        self.active = f"{name_prefix}-{new_tab_number}"
        self._focus_on_visible_table()

    def action_focus_data_catalog(self) -> None:
        if hasattr(self.app, "action_focus_data_catalog"):
            self.app.action_focus_data_catalog()

    def action_focus_query_editor(self) -> None:
        if hasattr(self.app, "action_focus_query_editor"):
            self.app.action_focus_query_editor()

    def _focus_on_visible_table(self) -> None:
        maybe_table = self.get_visible_table()
        if maybe_table is not None:
            maybe_table.focus()

    def _human_row_count(self, total_rows: int) -> str:
        if self.max_results > 0 and total_rows > self.max_results:
            return f"(Showing {self.max_results:,} of {total_rows:,} Records)"
        else:
            return f"({total_rows:,} Records)"

    def _format_column_label(
        self, col_name: str, col_type: str, sorted_descending: bool | None = None
    ) -> Text:
        """Build a header label, ending in the column's sort indicator.

        `sorted_descending` is None for a column we aren't sorted by, which gets
        the dim hint glyph. Every column carries a glyph so that sorting never
        changes a header's width.
        """
        type_label_style = self.get_component_rich_style("results-viewer--type-label")
        type_label_fg_style = Style(color=type_label_style.color)
        label = Text.assemble(col_name, " ", (col_type, type_label_fg_style))
        if sorted_descending is None:
            label.append(f" {SORT_HINT_GLYPH}", Style(dim=True))
        else:
            glyph = SORT_DESC_GLYPH if sorted_descending else SORT_ASC_GLYPH
            label.append(f" {glyph}", Style(bold=True))
        return label

    def _get_max_col_width(self) -> int:
        SMALLEST_MAX_WIDTH = 20
        CELL_X_PADDING = 2
        parent_size = getattr(self.parent, "container_size", self.screen.container_size)
        return max(SMALLEST_MAX_WIDTH, parent_size.width // 2 - CELL_X_PADDING)
