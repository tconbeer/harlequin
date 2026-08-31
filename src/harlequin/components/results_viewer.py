from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from rich.align import Align
from rich.cells import cell_len
from rich.console import RenderableType
from rich.style import Style
from rich.text import Text
from textual.coordinate import Coordinate
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

FOREIGN_KEY_GLYPH = "↗"


class ResultsTable(DataTable, inherit_bindings=False):
    DEFAULT_CSS = """
        ResultsTable {
            height: 100%;
            width: 100%;
        }
    """
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        *DataTable.COMPONENT_CLASSES,
        "results-table--foreign-key",
    }

    class ForeignKeyFollowed(Message):
        """The glyph on a foreign-key cell was clicked."""

        def __init__(self, ref_table: str, ref_col: str, value: Any) -> None:
            super().__init__()
            self.ref_table = ref_table
            self.ref_col = ref_col
            self.value = value

    def on_mount(self) -> None:
        # every value in a foreign-key column is rendered with the glyph beside it
        for column_index in self.foreign_keys:
            if self.is_valid_column_index(column_index):
                self.ordered_columns[column_index].content_width += cell_len(
                    f"{FOREIGN_KEY_GLYPH} "
                )
        self.post_message(WidgetMounted(widget=self))

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
        foreign_keys: dict[int, tuple[str, str]] | None = None,
    ):
        self.foreign_keys = foreign_keys or {}
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
        # the glyph is styled by its component class, not as a link
        self.auto_links = False

    def _get_cell_renderable(
        self, row_index: int, column_index: int, max_width: int | None = None
    ) -> RenderableType | Text:
        if row_index < 0 or column_index not in self.foreign_keys:
            return super()._get_cell_renderable(row_index, column_index, max_width)
        marker_width = cell_len(f"{FOREIGN_KEY_GLYPH} ")
        cell = super()._get_cell_renderable(
            row_index,
            column_index,
            None if max_width is None else max_width - marker_width,
        )
        if self.get_cell_at(Coordinate(row_index, column_index)) is None:
            return cell
        # clicking the glyph runs the action; the value itself stays a plain cell
        glyph_style = self.get_component_rich_style(
            "results-table--foreign-key"
        ) + Style.from_meta(
            {"@click": f"follow_foreign_key({row_index}, {column_index})"}
        )
        if isinstance(cell, Align):
            value = self._as_text(cell.renderable)
            if value is None:
                return cell
            # a right-aligned value keeps the glyph on the column's edge
            marked = (
                Text.assemble(value, " ", (FOREIGN_KEY_GLYPH, glyph_style))
                if cell.align == "right"
                else Text.assemble((FOREIGN_KEY_GLYPH, glyph_style), " ", value)
            )
            return Align(marked, align=cell.align, style=cell.style)
        value = self._as_text(cell)
        if value is None:
            return cell
        return Text.assemble((FOREIGN_KEY_GLYPH, glyph_style), " ", value)

    @staticmethod
    def _as_text(renderable: RenderableType) -> Text | None:
        """The formatter's str cells are console markup; anything else is left alone."""
        if isinstance(renderable, Text):
            return renderable
        if isinstance(renderable, str):
            return Text.from_markup(renderable)
        return None

    def action_follow_foreign_key(self, row_index: int, column_index: int) -> None:
        ref_table, ref_col = self.foreign_keys[column_index]
        self.post_message(
            self.ForeignKeyFollowed(
                ref_table=ref_table,
                ref_col=ref_col,
                value=self.get_cell_at(Coordinate(row_index, column_index)),
            )
        )

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

    async def push_table(self, table_id: str, result: ResultSet) -> ResultsTable:
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
            foreign_keys=result.foreign_keys,
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
