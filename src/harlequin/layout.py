"""Arranging already-serialized text for a reader.

The three layouts do padding, pipes and row counts, and nothing else. Their
strings come from `ResultSet.text_columns()`, the same duckdb cast that
`harlequin.export` writes a csv with, so a value reads the same on a terminal
as it does in a file.

`LayoutOptions` is a set of independent switches, following psql: `-t` is
`header=False, footer=False`, and `-A` is `aligned=False`.

One of them is a row cap, and it is a *soft* one: a layout is something a
person reads, and a thousand rows scrolled past is neither read nor useful.
Each layout declares the default that suits its shape -- `DEFAULT_MAX_ROWS`,
which a caller reads with `default_max_rows()` -- and it caps what is printed
only. The rows the result set holds are what the footer counts, so a capped
result reads `40 of 500 rows` and says what it is not showing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Protocol, Sequence, TextIO

if TYPE_CHECKING:
    from harlequin.query import ResultSet

Row = Sequence[str | None]
"""One row of already-serialized text. `None` is a SQL NULL.

Nulls stay `None` until the last moment, so that the literal string a caller
chose to render them as is never confused with the same string in the data.
"""

DEFAULT_NULL_STRING = "NULL"
"""How a SQL NULL renders when the caller has no preference.

Empty would be indistinguishable from the empty string, and telling those two
apart is most of what a reader is doing when it looks at a result.
"""

_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"


@dataclass(frozen=True)
class LayoutOptions:
    header: bool = True
    """Print column names."""

    footer: bool = True
    """Print the row count, and the truncation notice when there is one."""

    aligned: bool = True
    """Pad cells into columns. Unaligned output is `|`-delimited, as in psql."""

    null_string: str | None = None
    """How to render a SQL NULL. `None` means `DEFAULT_NULL_STRING`."""

    color: bool = False
    """Emit ANSI styling for the header and for nulls.

    Never for anything else: styling is applied inside a cell's padding, so it
    can never change what a column lines up with.
    """

    max_rows: int | None = None
    """Print at most this many rows. None prints every row the result holds.

    A soft cap: the rows are already fetched, so the footer still counts them
    all. `default_max_rows()` is the number each layout would choose.
    """


class Layout(Protocol):
    def write(self, result: "ResultSet", out: TextIO) -> None:
        """Write `result` to `out`, which must not translate newlines.

        A layout writes `\\n`, on every platform, because the bytes are the
        contract. A caller handing this a text stream is responsible for
        opening it with `newline=""`.
        """
        ...


def layout_names() -> list[str]:
    """Every layout name `get_layout()` accepts, aliases included."""
    return list(_LAYOUTS)


def default_max_rows(name: str) -> int:
    """How many rows `name` prints when its caller has no preference.

    Per layout rather than one number: `vertical` spends a line per column, so
    the ten records that fill a screen there are forty rows of a table.
    """
    try:
        return _LAYOUTS[name].DEFAULT_MAX_ROWS
    except KeyError as e:
        raise ValueError(f"{name} is not a layout Harlequin can render.") from e


def get_layout(name: str, options: LayoutOptions | None = None) -> Layout:
    try:
        layout = _LAYOUTS[name]
    except KeyError as e:
        raise ValueError(
            f"{name} is not a layout Harlequin can render. "
            f"Try one of: {', '.join(_LAYOUTS)}."
        ) from e
    return layout(options if options is not None else LayoutOptions())


class _BaseLayout:
    DEFAULT_MAX_ROWS = 40

    def __init__(self, options: LayoutOptions) -> None:
        self.options = options
        self.null = (
            options.null_string
            if options.null_string is not None
            else DEFAULT_NULL_STRING
        )

    def write(self, result: "ResultSet", out: TextIO) -> None:
        raise NotImplementedError

    def _headers(self, result: "ResultSet") -> list[str]:
        return [name for name, _ in result.columns]

    def _rows(self, result: "ResultSet") -> list[Row]:
        """The rows this layout prints, which the row cap may cut short.

        Cut here rather than at each layout, so that the columns are only as
        wide as what is printed: a value in a row nobody sees should not pad the
        rows they do.
        """
        columns = [column.to_pylist() for column in result.text_columns().columns]
        rows: list[Row] = (
            [list(row) for row in zip(*columns, strict=False)] if columns else []
        )
        cap = self.options.max_rows
        return rows if cap is None else rows[:cap]

    def _style(self, text: str, *, bold: bool = False, dim: bool = False) -> str:
        if not self.options.color:
            return text
        prefix = f"{_BOLD if bold else ''}{_DIM if dim else ''}"
        return f"{prefix}{text}{_RESET}" if prefix else text

    def _cell(self, value: str | None, width: int) -> str:
        """A value, rendered, styled if it is a null, and padded to `width`.

        In that order: an ANSI escape occupies no cells, so padding a styled
        string would count its bytes and misalign the column.
        """
        text = self.null if value is None else value
        return self._pad(self._style(text, dim=value is None), _width(text), width)

    def _pad(self, styled: str, plain_width: int, width: int) -> str:
        if not self.options.aligned:
            return styled
        return styled + " " * max(width - plain_width, 0)

    def _label(self, name: str, width: int) -> str:
        return self._pad(self._style(name, bold=True), _width(name), width)

    @staticmethod
    def _unpadded_last(widths: Sequence[int]) -> list[int]:
        """`widths`, with the final column's padding dropped.

        Padding the last column puts trailing spaces on every line, and
        rstripping the line afterwards is not the same thing: a value may
        legitimately end in a space, and trimming it would make `-F table`
        disagree with `-F csv` about the data.
        """
        return [*widths[:-1], 0] if widths else []

    def _footer(self, result: "ResultSet", shown: int) -> str:
        """The row count, as psql writes it.

        Three numbers can differ and the footer has to hold all of them: the
        rows printed, the rows fetched, and whether there were more. So a row
        cap reads `40 of 500 rows`, a hard fetch limit reads `500 of >500 rows`,
        and the two together read `40 of >500 rows`.

        Under a hard fetch limit the true total is unknowable -- not fetching it
        is the point of the limit -- so the footer must not invent one. `>500`
        and not `500+`: the limit+1 fetch proves there is a 501st row, so the
        total is strictly greater, where the conventional `N+` claims only
        `at least N` and would be the weaker statement of the two. The noun
        agrees with the total rather than with what was printed, which is why a
        truncated result is always plural.
        """
        rows = result.fetched_row_count
        if result.truncated:
            return f"({shown} of >{rows} rows)"
        if shown < rows:
            return f"({shown} of {rows} rows)"
        return f"({rows} {'row' if rows == 1 else 'rows'})"


class _TableLayout(_BaseLayout):
    """Aligned text, in the shape psql's default output has."""

    def write(self, result: "ResultSet", out: TextIO) -> None:
        headers = self._headers(result)
        rows = self._rows(result)
        widths = _column_widths(headers, rows, self.null)
        padding = self._unpadded_last(widths)
        aligned = self.options.aligned

        if self.options.header:
            labels = [
                self._label(name, w) for name, w in zip(headers, padding, strict=False)
            ]
            if aligned:
                out.write(" " + " | ".join(labels) + "\n")
                out.write("-" + "-+-".join("-" * w for w in widths) + "-\n")
            else:
                out.write("|".join(labels) + "\n")

        for row in rows:
            cells = [
                self._cell(value, w) for value, w in zip(row, padding, strict=False)
            ]
            if aligned:
                out.write(" " + " | ".join(cells) + "\n")
            else:
                out.write("|".join(cells) + "\n")

        if self.options.footer:
            out.write(self._footer(result, shown=len(rows)) + "\n")


class _MarkdownLayout(_BaseLayout):
    """A GitHub-flavored pipe table.

    The one layout that escapes what it prints: an unescaped `|` in a value
    would start a new cell, and a newline would end the row.
    """

    MIN_WIDTH = 3
    """The shortest delimiter row GFM accepts is `---`."""

    def write(self, result: "ResultSet", out: TextIO) -> None:
        headers = [_escape(name) for name in self._headers(result)]
        rows: list[Row] = [
            [None if value is None else _escape(value) for value in row]
            for row in self._rows(result)
        ]
        widths = [
            max(width, self.MIN_WIDTH)
            for width in _column_widths(headers, rows, _escape(self.null))
        ]

        if self.options.header:
            out.write(
                _pipe_row(
                    self._label(name, w)
                    for name, w in zip(headers, widths, strict=False)
                )
            )
            out.write(
                _pipe_row(
                    "-" * (w if self.options.aligned else self.MIN_WIDTH)
                    for w in widths
                )
            )

        for row in rows:
            out.write(
                _pipe_row(self._cell(v, w) for v, w in zip(row, widths, strict=False))
            )

        if self.options.footer:
            # italic and set off by a blank line, so it reads as a caption
            # rather than as a broken row.
            out.write(f"\n_{self._footer(result, shown=len(rows))}_\n")


class _VerticalLayout(_BaseLayout):
    """One column per line, in the shape psql's `\\x` produces.

    The layout to reach for when one row is wider than the terminal, which is
    the case that makes every other text layout unreadable.
    """

    DEFAULT_MAX_ROWS = 10
    """A record here is as many lines as the result has columns."""

    def write(self, result: "ResultSet", out: TextIO) -> None:
        headers = self._headers(result)
        rows = self._rows(result)
        name_width = max((_width(name) for name in headers), default=0)
        separator = " | " if self.options.aligned else "|"
        # one width for the whole result, not one per record: a rule that
        # changed length from record to record would read as raggedness rather
        # than as structure.
        rule_width = name_width + len(separator) + _widest(rows, self.null)

        for i, row in enumerate(rows, start=1):
            if self.options.header:
                out.write(self._record_rule(i, rule_width) + "\n")
            elif i > 1:
                # with the record rules suppressed, a blank line is all that
                # keeps two records from reading as one.
                out.write("\n")
            for name, value in zip(headers, row, strict=False):
                label = self._label(name, name_width)
                out.write(f"{label}{separator}{self._cell(value, 0)}\n")

        if self.options.footer:
            out.write(self._footer(result, shown=len(rows)) + "\n")

    def _record_rule(self, index: int, width: int) -> str:
        rule = f"-[ RECORD {index} ]"
        if not self.options.aligned:
            return rule
        return rule + "-" * max(width - len(rule), 1)


def _width(text: str) -> int:
    """How many terminal cells `text` occupies.

    Not `len()`: a CJK ideograph or an emoji is one character and two cells, and
    a combining mark is one character and none. A column measured in characters
    is a column that does not line up for anyone whose data isn't Latin.
    """
    if text.isascii():
        # the overwhelming majority of cells. `isascii()` is a C-level flag
        # check, and every printable ASCII character is exactly one cell.
        return len(text)
    # deferred, not module-scope: the fast path above returns without it, so an
    # all-ASCII run -- most of them -- never pays wcwidth's ~25ms import.
    from wcwidth import wcswidth

    width = wcswidth(text)
    # -1 means the string holds a control character, whose effect on the cursor
    # we cannot predict anyway -- that row is misaligned whatever we return, so
    # return the answer that at least stays deterministic.
    return width if width >= 0 else len(text)


def _column_widths(headers: Sequence[str], rows: Sequence[Row], null: str) -> list[int]:
    """The width of each column, in terminal cells."""
    widths = [_width(header) for header in headers]
    for row in rows:
        for i, value in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], _width(null if value is None else value))
    return widths


def _widest(rows: Sequence[Row], null: str) -> int:
    """The widest value anywhere in `rows`, in terminal cells."""
    return max(
        (_width(null if value is None else value) for row in rows for value in row),
        default=0,
    )


def _pipe_row(cells: Iterable[str]) -> str:
    return "| " + " | ".join(cells) + " |\n"


def _escape(text: str) -> str:
    """Keep a value inside its markdown cell.

    A literal `|` would start a new one; a newline would end the row.
    """
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


_LAYOUTS = {
    "table": _TableLayout,
    "markdown": _MarkdownLayout,
    "md": _MarkdownLayout,
    "vertical": _VerticalLayout,
}
