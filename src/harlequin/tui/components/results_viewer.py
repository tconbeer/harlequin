from textual.widgets import DataTable


class ResultsViewer(DataTable):
    def on_mount(self) -> None:
        self.border_title = "Query Results"
