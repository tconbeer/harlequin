from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from rich.console import RenderableType
from rich.style import Style
from rich.text import Text
from textual import events, on
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
from textual_fastdatatable.backend import AutoBackendType

from harlequin.messages import WidgetMounted

if TYPE_CHECKING:
    from textual_fastdatatable.backend import DataTableBackend
    from textual_fastdatatable.data_table import CursorType


class ResultsTable(DataTable, inherit_bindings=False):
    DEFAULT_CSS = """
        ResultsTable {
            height: 100%;
            width: 100%;
        }
    """

    class ForeignKeyFollowed(Message):
        """Posted when the user single-clicks a foreign-key cell to navigate to
        the referenced row."""

        def __init__(self, ref_table: str, ref_col: str, value: Any) -> None:
            self.ref_table = ref_table
            self.ref_col = ref_col
            self.value = value
            super().__init__()

    class CellEditRequested(Message):
        """Posted when the user double-clicks a cell to edit its value."""

        def __init__(self, row: int, column: int, value: Any) -> None:
            self.row = row
            self.column = column
            self.value = value
            super().__init__()

    # {result-column-index: (referenced_qualified_table, referenced_column)}
    fk_map: dict[int, tuple[str, str]] = {}
    # {result-column-index: (qualified_table, quoted_column, is_primary_key)} for
    # cells that map to a plain table column and can be edited in place.
    edit_map: dict[int, tuple[str, str, bool]] = {}
    _fk_glyph = " ↗"

    def on_mount(self) -> None:
        self.post_message(WidgetMounted(widget=self))
        self._pending_fk_timer: Any = None
        self._edited_cells: dict[tuple[int, int], Any] = {}

    def apply_edit(self, row: int, column: int, value: Any) -> None:
        """Overlay a new value for a cell after an in-place edit. The result
        backend is immutable, so edited values are kept in a side map and
        rendered (and returned by get_cell_at) from there."""
        self._edited_cells[(row, column)] = value
        self.refresh()

    def get_cell_at(self, coordinate: Coordinate) -> Any:
        edited = getattr(self, "_edited_cells", {})
        key = (coordinate.row, coordinate.column)
        if key in edited:
            return edited[key]
        return super().get_cell_at(coordinate)

    def _get_cell_renderable(
        self, row_index: int, column_index: int
    ) -> RenderableType | Text:
        """Render a foreign-key cell as its value plus a glyph, and edited cells
        from the overlay. get_cell_at reflects edits too, so navigation/copy use
        the current value. Everything else falls through to the default renderer."""
        edited = getattr(self, "_edited_cells", {})
        if row_index >= 0 and column_index in self.fk_map:
            raw = self.get_cell_at(Coordinate(row_index, column_index))
            if raw is not None:
                cell = Text(str(raw))
                cell.append(self._fk_glyph, Style(color="cyan", bold=True))
                return cell
        if (row_index, column_index) in edited:
            raw = edited[(row_index, column_index)]
            return Text("" if raw is None else str(raw))
        return super()._get_cell_renderable(row_index, column_index)

    @on(events.Click)
    def _handle_cell_click(self, event: events.Click) -> None:
        meta = event.style.meta
        if not meta:
            return
        row = meta.get("row")
        column = meta.get("column")
        if row is None or column is None or row < 0:  # header or gutter
            return
        chain = getattr(event, "chain", 1)
        if chain >= 2:
            # Double-click: cancel any pending FK navigation and request an edit.
            if self._pending_fk_timer is not None:
                self._pending_fk_timer.stop()
                self._pending_fk_timer = None
            value = self.get_cell_at(Coordinate(row, column))
            self.post_message(
                self.CellEditRequested(row=row, column=column, value=value)
            )
            event.stop()
            return
        # Single-click on a foreign-key cell navigates -- but debounced, so a
        # double-click (edit) can cancel it before it fires.
        if column not in self.fk_map:
            return
        value = self.get_cell_at(Coordinate(row, column))
        if value is None:  # can't navigate on a NULL fk
            return
        ref_table, ref_col = self.fk_map[column]

        def _navigate() -> None:
            self._pending_fk_timer = None
            self.post_message(
                self.ForeignKeyFollowed(
                    ref_table=ref_table, ref_col=ref_col, value=value
                )
            )

        if self._pending_fk_timer is not None:
            self._pending_fk_timer.stop()
        self._pending_fk_timer = self.set_timer(0.35, _navigate)
        event.stop()

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
    ):
        self.plain_column_labels: list[str] = (
            [str(label) for label in plain_column_labels]
            if plain_column_labels is not None
            else []
        )
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
        fk_map: dict[int, tuple[str, str]] | None = None,
        edit_map: dict[int, tuple[str, str, bool]] | None = None,
    ) -> ResultsTable:
        fk_map = fk_map or {}
        edit_map = edit_map or {}
        formatted_labels = [
            self._format_column_label(col_name, col_type)
            for col_name, col_type in column_labels
        ]
        table = ResultsTable(
            id=table_id,
            column_labels=formatted_labels,  # type: ignore
            plain_column_labels=[col_name for (col_name, _) in column_labels],
            data=data,
            max_rows=self.max_results,
            cursor_type="range",
            max_column_content_width=self.max_col_width,
            null_rep="[dim]∅ null[/]",
            render_markup=False,
        )
        table.fk_map = fk_map
        table.edit_map = edit_map
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
