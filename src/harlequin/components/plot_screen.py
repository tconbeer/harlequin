from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, List

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.validation import Integer
from textual.widgets import Footer, Input, Select, Static
from textual_plotext import PlotextPlot

from harlequin.components.error_modal import ErrorModal
from harlequin.data_prep import prepare_data
from harlequin.messages import WidgetMounted

if TYPE_CHECKING:
    # A bit unsure here why we need the ignore for type...
    import duckdb  # type: ignore[import-untyped]
    from textual.widgets._select import NoSelection

    from harlequin.components.results_viewer import ResultsTable

NUMBER_OF_PLOTS = 2
MAX_ELEM_DEFAULT = 1000


class PlotType(str, Enum):
    LINE = "Line"
    SCATTER = "Scatter"


@dataclass
class PlotMetadata:
    x: str = ""
    y: str = ""
    type: PlotType | None = PlotType.SCATTER
    # NOTE: if we try to plot too much stuff
    # the program might freeze badly. Considering
    # using a truly multithreaded solution might help.
    # I don't think we care in this case though
    max_elem: int = MAX_ELEM_DEFAULT


def parseSelect(val: Any) -> str:
    return str(val) if val != Select.BLANK else ""


class Plot(Static):
    def __init__(
        self,
        data: "duckdb.DuckDBPyRelation",
        metadata: PlotMetadata,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name if name is not None else "", id=id, classes=classes)
        self.data = data
        self.metadata = metadata

    def _is_col(self, c: str) -> bool:
        return c in self.data.columns

    def col_or_blank(self, c: str) -> str | "NoSelection":
        return Select.BLANK if not self._is_col(c) else c

    def compose(self) -> ComposeResult:
        self.input = Input(
            placeholder="Max elem displayed",
            value=str(self.metadata.max_elem),
            validators=[Integer(0)],
            id="max_elem",
        )
        with Vertical():
            with Horizontal():
                # TODO: new versions of textual support type_to_search.
                # As library deps are more complex to update, leave for later to do
                # But tbh, like this is also pretty ok :)
                yield Select(
                    options=[(t, t) for t in PlotType],
                    prompt="Plot Type",
                    id="plot_type",
                    value=self.metadata.type
                    if self.metadata.type is not None
                    else Select.BLANK,
                )
                yield Select(
                    options=[(col, col) for col in self.data.columns],
                    prompt="x",
                    id="x",
                    value=self.col_or_blank(self.metadata.x)
                    if not self._is_col(self.metadata.x)
                    else self.metadata.x,
                )
                yield Select(
                    options=[(col, col) for col in self.data.columns],
                    prompt="y",
                    id="y",
                    value=self.col_or_blank(self.metadata.y)
                    if not self._is_col(self.metadata.y)
                    else self.metadata.y,
                )
                yield self.input
            yield PlotextPlot()

    def on_mount(self) -> None:
        self._update()

    def _push_error_modal(self, header: str, error: BaseException) -> None:
        self.app.push_screen(
            ErrorModal(
                title="Plot Data Error",
                header=header,
                error=error,
            ),
        )

    @on(Select.Changed, "#plot_type")
    def type_changed(self, event: Select.Changed) -> None:
        self.metadata.type = (
            None if event.value == Select.BLANK else PlotType(event.value)
        )
        self._update()

    @on(Input.Submitted, "#max_elem")
    def max_elem_changed(self, event: Select.Changed) -> None:
        val = parseSelect(event.value)
        res = self.input.validate(val)
        if res and res.is_valid:
            self.input.blur()
            self.metadata.max_elem = int(val)
            self._update()
        else:
            self._push_error_modal(
                "Wrong max elements",
                ValueError("Use a positive integer"),
            )

    @on(Select.Changed, "#x")
    def x_changed(self, event: Select.Changed) -> None:
        self.metadata.x = parseSelect(event.value)
        self._update()

    @on(Select.Changed, "#y")
    def y_changed(self, event: Select.Changed) -> None:
        self.metadata.y = parseSelect(event.value)
        self._update()

    def _get_vals(self, col: str) -> List:
        # we go from numpy to list, otherwise strange things happen when having
        # float16, int8... (I don't think plotext handles them properly)
        return (  # type: ignore[no-any-return]
            self.data.limit(self.metadata.max_elem)
            .select(col)
            .fetchnumpy()[col]
            .tolist()
        )

    def _update(self) -> None:
        self.plotextplot = self.query_one(PlotextPlot)
        self.plt = self.plotextplot.plt
        meta = self.metadata
        # NOTE: we don't raise here because this might happen due to caching prev cols
        if meta.x not in self.data.columns or meta.y not in self.data.columns:
            return
        try:
            self.plt.clf()
            if meta.type == PlotType.LINE:
                self.plt.plot(self._get_vals(meta.x), self._get_vals(meta.y))
            elif meta.type == PlotType.SCATTER:
                self.plt.scatter(self._get_vals(meta.x), self._get_vals(meta.y))
        except Exception as e:
            self._push_error_modal("Failed while drawing", e)

        self.plotextplot.refresh()


# Important to be ModalScreen: we don't want
# to inherit main screen key-bindings
class PlotScreen(ModalScreen):
    def __init__(
        self,
        table: "ResultsTable",
        plots_metadata: List[PlotMetadata],
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id=id, classes=classes)
        import duckdb

        self.data = duckdb.arrow(prepare_data(table))  # type: ignore[arg-type]
        self.plot_data = plots_metadata
        self.full_screen = False

    def compose(self) -> ComposeResult:
        with Vertical(id="plot_outer"):
            for p in self.plot_data:
                yield Plot(self.data, p)
            yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        self.plots = self.query(Plot)
        self.post_message(WidgetMounted(widget=self))

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_toggle_full_screen(self) -> None:
        self.full_screen = not self.full_screen

        if not self.full_screen:
            for plot in self.plots:
                plot.disabled = False
            return

        focused = None
        for plot in self.plots:
            if plot.has_focus_within:
                focused = plot
        if focused is None:
            return
        focused.disabled = False
        for plot in filter(lambda x: x != focused, self.plots):
            plot.disabled = True
