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

from harlequin.components.text_modal import CellViewModal
from harlequin.messages import WidgetMounted

if TYPE_CHECKING:
    from textual_fastdatatable.backend import DataTableBackend
    from textual_fastdatatable.data_table import CursorType

    from harlequin.query import ResultSet


SORT_ASC_GLYPH = "▲"
SORT_DESC_GLYPH = "▼"


class ResultsTable(DataTable, inherit_bindings=False):
    DEFAULT_CSS = """
        ResultsTable {
            height: 100%;
            width: 100%;
        }
    """

    class SortRequested(Message):
        """A header was clicked: rewrite the statement to order by that column.

        `column_index` is None when the click asks for the statement as written.
        """

        def __init__(
            self,
            source_query: str,
            query_text: str,
            column_index: int | None,
            column_name: str,
            descending: bool,
            by_position: bool = False,
        ) -> None:
            super().__init__()
            self.source_query = source_query
            self.query_text = query_text
            self.column_index = column_index
            self.column_name = column_name
            self.descending = descending
            self.by_position = by_position
            """Order by the column's position: its name is computed or duplicated."""

    def on_mount(self) -> None:
        if self.sort_column is not None:
            self._show_sort_indicator()
        self.post_message(WidgetMounted(widget=self))

    @on(DataTable.HeaderSelected)
    def sort_by_header(self, event: DataTable.HeaderSelected) -> None:
        """Clicking a header cycles that column ascending, descending, as written."""
        event.stop()
        index = event.column_index
        if not self.is_valid_column_index(index) or not self.source_query:
            return
        if index >= len(self.plain_column_labels):
            return
        if self.sort_column != index:
            requested: tuple[int | None, bool] = (index, False)
        elif not self.sort_descending:
            requested = (index, True)
        else:
            requested = (None, False)
        name = self.plain_column_labels[index]
        self.post_message(
            self.SortRequested(
                source_query=self.source_query,
                query_text=self.query_text,
                column_index=requested[0],
                column_name=name,
                descending=requested[1],
                by_position=not name.isidentifier()
                or self.plain_column_labels.count(name) > 1,
            )
        )

    def __init__(
        self,
        *,
        backend: "DataTableBackend" | None = None,
        data: Any | None = None,
        column_labels: list[str | Text] | None = None,
        plain_column_labels: list[str | Text] | None = None,
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
        fetched_row_count: int | None = None,
        fetch_truncated: bool = False,
        source_query: str = "",
        query_text: str = "",
        sort_column: int | None = None,
        sort_descending: bool = False,
    ):
        self.plain_column_labels: list[str] = (
            [str(label) for label in plain_column_labels]
            if plain_column_labels is not None
            else []
        )
        # what the database returned, which `source_row_count` cannot say on its
        # own: under a hard fetch limit it counts the overflow probe row, and
        # there were more rows behind it that nobody fetched.
        self.fetched_row_count = fetched_row_count
        self.fetch_truncated = fetch_truncated
        self.source_query = source_query
        """The statement as the user wrote it, before any ordering was added."""
        self.query_text = query_text
        """The statement that produced these rows, as it stands in the editor."""
        self.sort_column = sort_column
        """Index of the column the rows are ordered by; None when unsorted."""
        self.sort_descending = sort_descending
        self._header_labels: dict[int, Text] = {}
        self._header_widths: dict[int, int] = {}
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

    def _show_sort_indicator(self) -> None:
        """Marks the sorted column's header, and gives it room for the mark."""
        for index, column in enumerate(self.ordered_columns):
            label = self._header_labels.setdefault(index, column.label)
            width = self._header_widths.setdefault(index, column.content_width)
            if index == self.sort_column:
                glyph = SORT_DESC_GLYPH if self.sort_descending else SORT_ASC_GLYPH
                column.label = Text.assemble(label, " ", glyph)
                column.content_width = max(width, self._measure(column.label))
            else:
                column.label = label
                column.content_width = width
        self._require_update_dimensions = True

    def action_view_cell(self) -> None:
        """Open a modal showing the full value of the cell under the cursor."""
        if self.backend is None or self.row_count == 0:
            return
        coord = self.cursor_coordinate
        if not self.is_valid_coordinate(coord):
            return
        value = self.get_cell_at(coord)
        try:
            column_label = self.plain_column_labels[coord.column]
        except IndexError:
            column_label = ""
        self.app.push_screen(CellViewModal(value=value, column_label=column_label))


class ResultsViewer(TabbedContent, can_focus=True):
    BORDER_TITLE = "Query Results"
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "results-viewer--type-label",
    }

    def __init__(self) -> None:
        super().__init__()

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
        result: ResultSet,
        source_query: str | None = None,
        sort_column: int | None = None,
        sort_descending: bool = False,
    ) -> ResultsTable:
        formatted_labels = [
            self._format_column_label(col_name, col_type)
            for col_name, col_type in result.columns
        ]
        table = ResultsTable(
            id=table_id,
            column_labels=formatted_labels,  # type: ignore
            plain_column_labels=[col_name for (col_name, _) in result.columns],
            # the backend was built by `harlequin.query.fetch()`, which already
            # applied `viewer_max_rows` as its row cap.
            backend=result.backend,
            fetched_row_count=result.fetched_row_count,
            fetch_truncated=result.truncated,
            # a sorted re-run keeps the statement the user wrote as its source
            source_query=source_query or result.statement.sql,
            query_text=result.statement.sql,
            sort_column=sort_column,
            sort_descending=sort_descending,
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
                if table.source_row_count > 0:
                    self.border_title = f"Query Results {self._human_row_count(table)}"
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
            self.border_title = f"Query Results {self._human_row_count(maybe_table)}"
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

    def _human_row_count(self, table: ResultsTable) -> str:
        """What the table holds, and what it is holding it out of.

        A hard fetch limit stops the total from being knowable -- not fetching
        the rest is the point of it -- so a truncated fetch reads `>500` rather
        than claiming the 500 rows that arrived were all there were.
        """
        shown = table.row_count
        total = (
            table.fetched_row_count
            if table.fetched_row_count is not None
            else table.source_row_count
        )
        if table.fetch_truncated:
            return f"(Showing {shown:,} of >{total:,} Records)"
        if shown < total:
            return f"(Showing {shown:,} of {total:,} Records)"
        return f"({total:,} Records)"

    def _format_column_label(self, col_name: str, col_type: str) -> Text:
        type_label_style = self.get_component_rich_style("results-viewer--type-label")
        type_label_fg_style = Style(color=type_label_style.color)
        label = Text.assemble(col_name, " ", (col_type, type_label_fg_style))
        return label

    def _get_max_col_width(self) -> int:
        SMALLEST_MAX_WIDTH = 20
        CELL_X_PADDING = 2
        parent_size = getattr(self.parent, "container_size", self.screen.container_size)
        return max(SMALLEST_MAX_WIDTH, parent_size.width // 2 - CELL_X_PADDING)
