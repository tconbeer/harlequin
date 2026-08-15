from __future__ import annotations

from typing import Any
from unittest import mock

import pyarrow as pa
import pytest

from harlequin.adapter import HarlequinAdapter, HarlequinConnection, HarlequinCursor
from harlequin.exception import HarlequinQueryError
from harlequin.query import ExecutedStatement, ResultSet, RowLimit, execute, fetch
from harlequin.statements import Statement, split


@pytest.fixture
def connection(duckdb_adapter: type[HarlequinAdapter]) -> HarlequinConnection:
    return duckdb_adapter([":memory:"], no_init=True).connect()


def statements(script: str) -> list[Statement]:
    return split(script)


class TestRowLimit:
    def test_unlimited_asks_for_everything(self) -> None:
        assert RowLimit().fetch_limit is None
        assert RowLimit(max_rows=None, detect_overflow=True).fetch_limit is None

    def test_plain_limit_asks_for_exactly_that_many(self) -> None:
        assert RowLimit(max_rows=500).fetch_limit == 500

    def test_overflow_detection_asks_for_one_more(self) -> None:
        """Exactly n rows back from set_limit(n) is ambiguous; n+1 is not."""
        assert RowLimit(max_rows=500, detect_overflow=True).fetch_limit == 501


class TestExecute:
    def test_yields_one_result_per_statement(
        self, connection: HarlequinConnection
    ) -> None:
        executed = list(execute(connection, statements("select 1; select 2; select 3")))
        assert [e.statement.index for e in executed] == [0, 1, 2]
        assert all(e.has_result_set for e in executed)
        assert all(e.error is None for e in executed)

    def test_ddl_yields_no_cursor(self, connection: HarlequinConnection) -> None:
        executed = list(
            execute(connection, statements("create table foo (a int); select 1"))
        )
        assert not executed[0].has_result_set
        assert executed[0].error is None
        assert executed[1].has_result_set

    def test_is_lazy(self, connection: HarlequinConnection) -> None:
        """A caller that stops consuming stops the script."""
        script = statements("create table foo (a int); create table bar (a int)")
        next(iter(execute(connection, script)))
        cur = connection.execute("select count(*) from duckdb_tables()")
        assert cur is not None
        assert cur.fetchall().to_pylist() == [{"count_star()": 1}]  # type: ignore[union-attr]

    def test_stops_at_the_first_error_by_default(
        self, connection: HarlequinConnection
    ) -> None:
        executed = list(execute(connection, statements("select 1; sel; select 3")))
        assert len(executed) == 2
        assert executed[0].error is None
        assert isinstance(executed[1].error, HarlequinQueryError)
        assert executed[1].statement.sql == "sel;"
        assert not executed[1].has_result_set

    def test_continues_past_an_error_when_asked(
        self, connection: HarlequinConnection
    ) -> None:
        executed = list(
            execute(
                connection, statements("select 1; sel; select 3"), on_error="continue"
            )
        )
        assert len(executed) == 3
        assert [e.error is None for e in executed] == [True, False, True]

    def test_applies_the_fetch_limit(self, connection: HarlequinConnection) -> None:
        (executed,) = execute(
            connection,
            statements("select * from range(100)"),
            limit=RowLimit(max_rows=5),
        )
        assert fetch(executed).row_count == 5

    def test_overflow_detection_fetches_one_extra_row(
        self, connection: HarlequinConnection
    ) -> None:
        (executed,) = execute(
            connection,
            statements("select * from range(100)"),
            limit=RowLimit(max_rows=5, detect_overflow=True),
        )
        # the cursor holds 6; the result set keeps 5 and knows there were more.
        assert fetch(executed).row_count == 6


class TestFetch:
    def test_returns_columns_and_rows(self, connection: HarlequinConnection) -> None:
        (executed,) = execute(connection, statements("select 1 as a, 'x' as b"))
        result = fetch(executed)
        assert [name for name, _ in result.columns] == ["a", "b"]
        assert result.row_count == 1
        assert result.backend.source_data.to_pylist() == [{"a": 1, "b": "x"}]
        assert result.statement.sql == "select 1 as a, 'x' as b"
        assert result.elapsed >= 0

    def test_a_query_that_matches_nothing_returns_zero_rows(
        self, connection: HarlequinConnection
    ) -> None:
        """Zero rows is not an error, and must not read like one."""
        (executed,) = execute(connection, statements("select 1 where false"))
        result = fetch(executed)
        assert result.columns == [("1", "#")]
        assert result.row_count == 0
        assert result.backend.source_data.to_pylist() == []
        assert result.truncated is False

    def test_a_cursor_that_returns_none_still_gets_its_columns(self) -> None:
        """The adapter interface lets a cursor return None instead of an empty
        result. It still described its columns, and `create_backend()` is given
        those, so the result is an empty table with a header rather than
        nothing at all."""

        class EmptyCursor(HarlequinCursor):
            def __init__(self) -> None:
                pass

            def columns(self) -> list[tuple[str, str]]:
                return [("a", "int")]

            def set_limit(self, limit: int) -> HarlequinCursor:
                return self

            def fetchall(self) -> Any:
                return None

        result = fetch(
            ExecutedStatement(statement=Statement("select 1", 0), cursor=EmptyCursor())
        )
        assert result.row_count == 0
        assert result.truncated is False
        assert result.arrow_table().column_names == ["a"]
        assert result.arrow_table().num_rows == 0

    def test_rejects_a_statement_with_no_cursor(
        self, connection: HarlequinConnection
    ) -> None:
        (executed,) = execute(connection, statements("create table foo (a int)"))
        with pytest.raises(ValueError):
            fetch(executed)

    def test_the_row_cap_is_not_the_fetch_limit(
        self, connection: HarlequinConnection
    ) -> None:
        """The app fetches everything and caps what it displays, so the backend
        knows the exact total -- which is what lets it say '5 of 100'."""
        (executed,) = execute(connection, statements("select * from range(100)"))
        result = fetch(executed, display_limit=5)
        assert result.row_count == 5
        assert result.fetched_row_count == 100
        assert result.truncated is False

    def test_the_display_cap_applies_over_the_fetch_limit(
        self, connection: HarlequinConnection
    ) -> None:
        """Both at once: fifty fetched of an unknown number, five kept of the
        fifty -- and the extra row that proved there were more is in neither."""
        limit = RowLimit(max_rows=50, detect_overflow=True)
        (executed,) = execute(
            connection, statements("select * from range(100)"), limit=limit
        )
        result = fetch(executed, limit=limit, display_limit=5)
        assert result.row_count == 5
        assert result.fetched_row_count == 50
        assert result.truncated is True


class TestTruncation:
    @pytest.mark.parametrize(
        ("rows", "expected_truncated", "expected_count"),
        [(4, False, 4), (5, False, 5), (6, True, 5)],
        ids=["under limit", "exactly at limit", "one over limit"],
    )
    def test_truncation_is_detected_from_one_extra_row(
        self,
        all_adapters: type[HarlequinAdapter],
        rows: int,
        expected_truncated: bool,
        expected_count: int,
    ) -> None:
        """duckdb and sqlite implement set_limit differently enough to matter."""
        connection = all_adapters([":memory:"], no_init=True).connect()
        limit = RowLimit(max_rows=5, detect_overflow=True)
        script = " union all ".join(f"select {i} as a" for i in range(rows))
        (executed,) = execute(connection, statements(script), limit=limit)
        result = fetch(executed, limit=limit)
        assert result.row_count == expected_count
        assert result.truncated is expected_truncated

    def test_an_unlimited_fetch_is_never_truncated(
        self, connection: HarlequinConnection
    ) -> None:
        limit = RowLimit(max_rows=None, detect_overflow=True)
        (executed,) = execute(
            connection, statements("select * from range(100)"), limit=limit
        )
        result = fetch(executed, limit=limit)
        assert result.row_count == 100
        assert result.truncated is False

    def test_a_limit_of_zero_rows_still_detects_the_rest(
        self, all_adapters: type[HarlequinAdapter]
    ) -> None:
        """`limit 0` asks what the columns are, and gets an answer that does
        not pretend the table was empty."""
        connection = all_adapters([":memory:"], no_init=True).connect()
        limit = RowLimit(max_rows=0, detect_overflow=True)
        (executed,) = execute(
            connection, statements("select 1 as a union all select 2"), limit=limit
        )
        result = fetch(executed, limit=limit)
        assert result.row_count == 0
        assert result.fetched_row_count == 0
        assert result.truncated is True
        assert result.arrow_table().column_names == ["a"]

    def test_a_limit_of_zero_over_an_empty_result_is_not_truncation(
        self, all_adapters: type[HarlequinAdapter]
    ) -> None:
        """The probe row is what makes truncation knowable, and there was none
        to fetch, so `limit 0` over an empty result is exactly empty."""
        connection = all_adapters([":memory:"], no_init=True).connect()
        limit = RowLimit(max_rows=0, detect_overflow=True)
        (executed,) = execute(
            connection, statements("select 1 as a where false"), limit=limit
        )
        result = fetch(executed, limit=limit)
        assert result.row_count == 0
        assert result.fetched_row_count == 0
        assert result.truncated is False

    def test_a_soft_cap_is_not_truncation(
        self, connection: HarlequinConnection
    ) -> None:
        """Nothing was left in the database, so nothing was cut short."""
        (executed,) = execute(connection, statements("select * from range(100)"))
        result = fetch(executed, display_limit=5)
        assert result.truncated is False


class TestArrowTable:
    def test_names_come_from_the_cursor(
        self, all_adapters: type[HarlequinAdapter]
    ) -> None:
        """An adapter that returns rows as tuples normalizes to columns named
        `f0`, `f1`, ... . The names the cursor described are the real ones."""
        connection = all_adapters([":memory:"], no_init=True).connect()
        (executed,) = execute(connection, statements("select 1 as a, 'x' as b"))
        assert fetch(executed).arrow_table().column_names == ["a", "b"]

    def test_duplicate_names_are_made_unique(
        self, all_adapters: type[HarlequinAdapter]
    ) -> None:
        """Arrow allows duplicates and `to_pylist()` silently drops them, so
        the backend resolves them. `export.write_file()` resolves them the same
        way, so it does not matter which of the two saw the table first."""
        connection = all_adapters([":memory:"], no_init=True).connect()
        (executed,) = execute(connection, statements("select 1 as a, 2 as a"))
        assert fetch(executed).arrow_table().column_names == ["a", "a0"]
        # the names the cursor reported are still available, verbatim
        assert [name for name, _ in fetch(executed).columns] == ["a", "a"]

    def test_zero_rows_keeps_its_columns(
        self, all_adapters: type[HarlequinAdapter]
    ) -> None:
        """Zero rows still has a header, whether the cursor returned an empty
        result or nothing at all."""
        connection = all_adapters([":memory:"], no_init=True).connect()
        (executed,) = execute(
            connection, statements("select 1 as a, 'x' as b where false")
        )
        table = fetch(executed).arrow_table()
        assert table.column_names == ["a", "b"]
        assert table.num_rows == 0

    def test_it_holds_the_rows_kept_not_the_rows_fetched(
        self, connection: HarlequinConnection
    ) -> None:
        """Under overflow detection one extra row was fetched to prove there
        were more. It is not part of the result."""
        limit = RowLimit(max_rows=5, detect_overflow=True)
        (executed,) = execute(
            connection, statements("select * from range(100)"), limit=limit
        )
        result = fetch(executed, limit=limit)
        assert result.truncated is True
        assert result.arrow_table().num_rows == 5


class TestTextColumns:
    def test_every_value_is_a_string(self, connection: HarlequinConnection) -> None:
        (executed,) = execute(
            connection, statements("select 1 as a, 2.5::double as b, true as c")
        )
        text = fetch(executed).text_columns()
        assert all(column.type == pa.string() for column in text.columns)
        assert text.to_pylist() == [{"a": "1", "b": "2.5", "c": "true"}]

    def test_duckdb_serializes_not_python(
        self, connection: HarlequinConnection
    ) -> None:
        """`str()` on a blob is a Python repr (`b'\\x00\\xff'`) and on a bool is
        `True`. Neither belongs in output an agent parses."""
        (executed,) = execute(
            connection,
            statements("select '\\x00\\xFF'::blob as b, false as f"),
        )
        assert fetch(executed).text_columns().to_pylist() == [
            {"b": "\\x00\\xFF", "f": "false"}
        ]

    def test_a_null_stays_a_null(self, connection: HarlequinConnection) -> None:
        """So that it stays distinguishable from the literal string a caller
        chose to render nulls as."""
        (executed,) = execute(connection, statements("select null as a, 'NULL' as b"))
        assert fetch(executed).text_columns().to_pylist() == [{"a": None, "b": "NULL"}]

    def test_duplicate_names_survive_the_cast(
        self, connection: HarlequinConnection
    ) -> None:
        (executed,) = execute(connection, statements("select 1 as a, 2 as a"))
        text = fetch(executed).text_columns()
        assert text.column_names == ["a", "a0"]
        assert [column.to_pylist() for column in text.columns] == [["1"], ["2"]]

    def test_zero_rows_is_not_an_error(self, connection: HarlequinConnection) -> None:
        (executed,) = execute(connection, statements("select 1 as a where false"))
        text = fetch(executed).text_columns()
        assert text.column_names == ["a"]
        assert text.num_rows == 0

    def test_a_type_duckdb_cannot_ingest_falls_back_loudly(self) -> None:
        """Not an expected path -- duckdb ingests every Arrow type these
        adapters have been seen to produce -- but a silent fallback would mean
        output that quietly disagreed with an exported file."""

        class UningestibleCursor(HarlequinCursor):
            def __init__(self) -> None:
                pass

            def columns(self) -> list[tuple[str, str]]:
                return [("a", "int")]

            def set_limit(self, limit: int) -> HarlequinCursor:
                return self

            def fetchall(self) -> Any:
                return pa.table({"a": [1, 2]})

        result = fetch(
            ExecutedStatement(
                statement=Statement("select 1", 0), cursor=UningestibleCursor()
            )
        )
        with mock.patch("duckdb.from_arrow", side_effect=RuntimeError("no such type")):
            with pytest.warns(RuntimeWarning, match="falling back"):
                text = result.text_columns()
        assert text.to_pylist() == [{"a": "1"}, {"a": "2"}]


class TestExecuteWithoutAnAdapter:
    """`execute()` is defined against the adapter interface, not against
    duckdb, so a driver that misbehaves is worth exercising directly."""

    def test_a_raw_driver_exception_does_not_escape(self) -> None:
        class Exploding(HarlequinConnection):
            def __init__(self) -> None:
                pass

            def execute(self, query: str) -> HarlequinCursor | None:
                raise RuntimeError("the driver did not read the docs")

            def get_catalog(self) -> Any:
                raise NotImplementedError

        (executed,) = execute(Exploding(), statements("select 1"))
        assert isinstance(executed.error, RuntimeError)

    def test_keyboard_interrupt_propagates(self) -> None:
        """Ctrl-C is not a query error: it must not be reported as one, and it
        must not be swallowed by on_error='continue'."""

        class Interrupting(HarlequinConnection):
            def __init__(self) -> None:
                pass

            def execute(self, query: str) -> HarlequinCursor | None:
                raise KeyboardInterrupt

            def get_catalog(self) -> Any:
                raise NotImplementedError

        with pytest.raises(KeyboardInterrupt):
            list(execute(Interrupting(), statements("select 1"), on_error="continue"))


@pytest.mark.parametrize(
    "returned",
    [
        {"a": [1, 2, 3]},
        [(1,), (2,), (3,)],
        pa.table({"a": [1, 2, 3]}),
        pa.RecordBatch.from_arrays([pa.array([1, 2, 3])], names=["a"]),
    ],
    ids=["mapping", "records", "table", "record batch"],
)
def test_every_shape_a_cursor_may_return_is_normalized(returned: Any) -> None:
    """`fetchall()` is typed `AutoBackendType`, which is `Any`. Routing each
    shape through the one `create_backend()` the app already uses is what keeps
    two front ends from disagreeing about what a row is."""

    class FakeCursor(HarlequinCursor):
        def __init__(self) -> None:
            pass

        def columns(self) -> list[tuple[str, str]]:
            return [("a", "int")]

        def set_limit(self, limit: int) -> HarlequinCursor:
            return self

        def fetchall(self) -> Any:
            return returned

    result = fetch(
        ExecutedStatement(statement=Statement("select 1", 0), cursor=FakeCursor())
    )
    assert isinstance(result, ResultSet)
    assert result.row_count == 3
    assert [
        next(iter(row.values())) for row in result.backend.source_data.to_pylist()
    ] == [1, 2, 3]
