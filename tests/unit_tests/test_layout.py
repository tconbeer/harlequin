from __future__ import annotations

import io
from typing import Callable

import pytest

from harlequin.layout import LayoutOptions, get_layout, layout_names
from harlequin.query import ResultSet, RowLimit

ResultSetFactory = Callable[..., ResultSet]


def render(
    result: ResultSet, name: str = "table", options: LayoutOptions | None = None
) -> str:
    out = io.StringIO()
    get_layout(name, options).write(result, out)
    return out.getvalue()


class TestGetLayout:
    def test_every_name_resolves(self) -> None:
        assert layout_names() == ["table", "markdown", "md", "vertical"]
        for name in layout_names():
            assert get_layout(name) is not None

    def test_md_is_markdown(self, result_set: ResultSetFactory) -> None:
        result = result_set("select 1 as a")
        assert render(result, "md") == render(result, "markdown")

    def test_an_unknown_name_names_the_known_ones(self) -> None:
        with pytest.raises(ValueError, match="table, markdown, md, vertical"):
            get_layout("yaml")


class TestTableLayout:
    def test_aligned_columns(self, result_set: ResultSetFactory) -> None:
        result = result_set(
            "select * from (values (1, 'alice'), (22, 'bob')) t(id, name)"
        )
        assert render(result) == (
            " id | name\n----+-------\n 1  | alice\n 22 | bob\n(2 rows)\n"
        )

    def test_tuples_only_drops_the_chrome(self, result_set: ResultSetFactory) -> None:
        result = result_set("select 1 as a, 'x' as b")
        assert render(result, options=LayoutOptions(header=False, footer=False)) == (
            " 1 | x\n"
        )

    def test_unaligned_is_pipe_delimited(self, result_set: ResultSetFactory) -> None:
        result = result_set("select 1 as a, 'x' as b")
        assert render(result, options=LayoutOptions(aligned=False)) == (
            "a|b\n1|x\n(1 row)\n"
        )

    def test_the_scalar_idiom(self, result_set: ResultSetFactory) -> None:
        """`hsql -tAc "select count(*)"` is the first thing a scripting
        audience will try, and it has to return a bare number."""
        result = result_set("select count(*) from range(41)")
        assert (
            render(
                result,
                options=LayoutOptions(header=False, footer=False, aligned=False),
            )
            == "41\n"
        )

    def test_zero_rows_keeps_its_header(self, result_set: ResultSetFactory) -> None:
        """Zero rows is not an error, and must not read like one."""
        result = result_set("select 1 as a, 'x' as b where false")
        assert render(result) == " a | b\n---+---\n(0 rows)\n"

    def test_the_last_column_is_not_padded(self, result_set: ResultSetFactory) -> None:
        """A value may legitimately end in a space, so the line cannot be
        rstripped -- the padding just isn't written."""
        result = result_set("select * from (values ('trailing  '), ('x')) t(padded)")
        assert render(result, options=LayoutOptions(footer=False)) == (
            " padded\n------------\n trailing  \n x\n"
        )


class TestMarkdownLayout:
    def test_pipe_table(self, result_set: ResultSetFactory) -> None:
        result = result_set(
            "select * from (values (1, 'alice'), (22, 'bob')) t(id, name)"
        )
        assert render(result, "markdown") == (
            "| id  | name  |\n"
            "| --- | ----- |\n"
            "| 1   | alice |\n"
            "| 22  | bob   |\n"
            "\n"
            "_(2 rows)_\n"
        )

    def test_a_cell_cannot_escape_its_column(
        self, result_set: ResultSetFactory
    ) -> None:
        """A `|` or a newline in a value doesn't make a table hard to read; it
        makes it a different table."""
        result = result_set("select 'a|b' as piped, 'one\ntwo' as multiline")
        rendered = render(result, "markdown", LayoutOptions(footer=False))
        assert "a\\|b" in rendered
        assert "one<br>two" in rendered
        assert len(rendered.splitlines()) == 3

    def test_columns_are_at_least_three_wide(
        self, result_set: ResultSetFactory
    ) -> None:
        """`---` is the shortest delimiter row GFM accepts."""
        result = result_set("select 1 as a")
        assert render(result, "markdown", LayoutOptions(footer=False)) == (
            "| a   |\n| --- |\n| 1   |\n"
        )


class TestVerticalLayout:
    def test_one_column_per_line(self, result_set: ResultSetFactory) -> None:
        result = result_set(
            "select * from (values (1, 'alice'), (22, 'bob')) t(id, name)"
        )
        assert render(result, "vertical") == (
            "-[ RECORD 1 ]-\n"
            "id   | 1\n"
            "name | alice\n"
            "-[ RECORD 2 ]-\n"
            "id   | 22\n"
            "name | bob\n"
            "(2 rows)\n"
        )

    def test_the_record_rule_is_one_width(self, result_set: ResultSetFactory) -> None:
        """A rule that changed length from record to record would read as
        raggedness rather than as structure."""
        result = result_set(
            "select * from (values ('x'), ('a much longer value')) t(v)"
        )
        rules = [
            line for line in render(result, "vertical").splitlines() if "RECORD" in line
        ]
        assert len(rules) == 2
        assert len({len(rule) for rule in rules}) == 1

    def test_without_a_header_records_are_blank_line_separated(
        self, result_set: ResultSetFactory
    ) -> None:
        result = result_set("select * from (values (1), (2)) t(a)")
        assert render(
            result, "vertical", LayoutOptions(header=False, footer=False)
        ) == ("a | 1\n\na | 2\n")


class TestNulls:
    def test_a_null_is_not_an_empty_string(self, result_set: ResultSetFactory) -> None:
        result = result_set("select null as a, '' as b")
        assert render(result, options=LayoutOptions(footer=False)) == (
            " a    | b\n------+---\n NULL | \n"
        )

    @pytest.mark.parametrize("name", ["table", "markdown", "vertical"])
    def test_the_null_string_is_the_callers_choice(
        self, result_set: ResultSetFactory, name: str
    ) -> None:
        result = result_set("select null as a")
        rendered = render(result, name, LayoutOptions(null_string="∅", footer=False))
        assert "∅" in rendered
        assert "NULL" not in rendered

    def test_an_empty_null_string_is_honored(
        self, result_set: ResultSetFactory
    ) -> None:
        result = result_set("select null as a, 1 as b")
        assert render(
            result, options=LayoutOptions(null_string="", header=False, footer=False)
        ) == ("   | 1\n")

    def test_the_literal_string_null_is_not_a_null(
        self, result_set: ResultSetFactory
    ) -> None:
        result = result_set("select 'NULL' as a, null as b")
        assert render(
            result, options=LayoutOptions(null_string="~", header=False, footer=False)
        ) == (" NULL | ~\n")


class TestFooter:
    def test_one_row_is_singular(self, result_set: ResultSetFactory) -> None:
        assert render(result_set("select 1")).endswith("(1 row)\n")

    def test_a_truncated_result_never_claims_a_total(
        self, result_set: ResultSetFactory
    ) -> None:
        """Not fetching the rest is the point of a hard limit, so the total is
        unknowable and the footer must not invent one."""
        limit = RowLimit(max_rows=2, detect_overflow=True)
        result = result_set("select * from range(10)", limit=limit)
        assert render(result).endswith("(2 of 2+ rows)\n")

    @pytest.mark.parametrize("name", ["table", "markdown", "vertical"])
    def test_every_layout_reports_truncation(
        self, result_set: ResultSetFactory, name: str
    ) -> None:
        limit = RowLimit(max_rows=2, detect_overflow=True)
        result = result_set("select * from range(10)", limit=limit)
        assert "(2 of 2+ rows)" in render(result, name)


class TestColor:
    def test_off_by_default(self, result_set: ResultSetFactory) -> None:
        assert "\x1b" not in render(result_set("select null as a"))

    def test_styles_the_header_and_nulls_only(
        self, result_set: ResultSetFactory
    ) -> None:
        result = result_set("select null as a, 'x' as b")
        rendered = render(result, options=LayoutOptions(color=True, footer=False))
        assert "\x1b[1ma\x1b[0m" in rendered
        assert "\x1b[2mNULL\x1b[0m" in rendered
        assert "\x1b[" not in rendered.split("| ")[-1]

    @pytest.mark.parametrize("name", ["table", "markdown", "vertical"])
    def test_color_does_not_move_a_column(
        self, result_set: ResultSetFactory, name: str
    ) -> None:
        """Styling is applied inside a cell's padding, so stripping the escapes
        gets the uncolored layout back exactly."""
        result = result_set(
            "select * from (values (1, null), (22, 'a value')) t(id, note)"
        )
        colored = render(result, name, LayoutOptions(color=True))
        for escape in ("\x1b[1m", "\x1b[2m", "\x1b[0m"):
            colored = colored.replace(escape, "")
        assert colored == render(result, name)


class TestLocale:
    def test_numbers_are_not_grouped(self, result_set: ResultSetFactory) -> None:
        """The data table's cell formatter renders numbers with `f"{n:n}"`,
        which is locale-aware. A layout that reached for it would print
        `1,234,567` on one machine and `1.234.567` on another."""
        result = result_set("select 1234567 as n, 1234567.89::double as d")
        rendered = render(result, options=LayoutOptions(header=False, footer=False))
        assert rendered == " 1234567 | 1234567.89\n"
