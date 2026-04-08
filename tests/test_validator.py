# tests/test_validator.py
import pytest
from unittest.mock import MagicMock

from validator import ValidationResult, get_row_count, validate_row_count, _strip_option_clause


def _make_conn(row_value=None, raise_exc=None):
    cursor = MagicMock()
    if raise_exc is not None:
        cursor.execute.side_effect = raise_exc
    else:
        cursor.fetchone.return_value = (row_value,)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


class TestGetRowCount:
    def test_returns_integer_from_cursor(self):
        conn, _ = _make_conn(row_value=42)
        result = get_row_count("SELECT * FROM t", conn)
        assert result == 42

    def test_cursor_is_closed_after_success(self):
        conn, cursor = _make_conn(row_value=10)
        get_row_count("SELECT 1", conn)
        cursor.close.assert_called_once()

    def test_cursor_is_closed_after_exception(self):
        conn, cursor = _make_conn(raise_exc=Exception("timeout"))
        with pytest.raises(Exception, match="timeout"):
            get_row_count("SELECT 1", conn)
        cursor.close.assert_called_once()

    def test_wraps_query_in_count_subquery(self):
        conn, cursor = _make_conn(row_value=0)
        get_row_count("SELECT * FROM [Sales].[Orders]", conn)
        call_sql = cursor.execute.call_args[0][0]
        assert "COUNT(*)" in call_sql
        assert "SELECT * FROM [Sales].[Orders]" in call_sql
        assert "_v" in call_sql

    def test_exception_propagates(self):
        conn, _ = _make_conn(raise_exc=RuntimeError("DB error"))
        with pytest.raises(RuntimeError, match="DB error"):
            get_row_count("SELECT 1", conn)


class TestValidateRowCount:
    def test_matching_counts_returns_valid(self):
        conn, _ = _make_conn(row_value=100)
        result = validate_row_count(100, "SELECT * FROM t", conn)
        assert result.is_valid is True
        assert result.base_count == 100
        assert result.variant_count == 100
        assert result.message == "OK"

    def test_mismatched_counts_returns_invalid(self):
        conn, _ = _make_conn(row_value=200)
        result = validate_row_count(100, "SELECT * FROM t", conn)
        assert result.is_valid is False
        assert result.base_count == 100
        assert result.variant_count == 200
        assert "100" in result.message
        assert "200" in result.message

    def test_exception_during_count_returns_valid_with_warning(self):
        conn, _ = _make_conn(raise_exc=Exception("connection lost"))
        result = validate_row_count(50, "SELECT * FROM t", conn)
        assert result.is_valid is True
        assert result.base_count == 50
        assert result.variant_count == -1
        assert "connection lost" in result.message

    def test_returns_validation_result_dataclass(self):
        conn, _ = _make_conn(row_value=5)
        result = validate_row_count(5, "SELECT 1", conn)
        assert isinstance(result, ValidationResult)

    def test_zero_rows_base_and_variant_is_valid(self):
        conn, _ = _make_conn(row_value=0)
        result = validate_row_count(0, "SELECT * FROM empty_table", conn)
        assert result.is_valid is True

    def test_option_clause_stripped_before_count(self):
        conn, cursor = _make_conn(row_value=100)
        result = validate_row_count(100, "SELECT * FROM t OPTION (RECOMPILE)", conn)
        assert result.is_valid is True
        assert result.variant_count == 100
        call_sql = cursor.execute.call_args[0][0]
        assert "OPTION" not in call_sql


class TestStripOptionClause:
    def test_strips_option_recompile(self):
        assert _strip_option_clause("SELECT 1 OPTION (RECOMPILE)") == "SELECT 1"

    def test_strips_option_hash_join(self):
        assert _strip_option_clause("SELECT * FROM t OPTION (HASH JOIN)") == "SELECT * FROM t"

    def test_strips_option_merge_join(self):
        assert _strip_option_clause("SELECT * FROM t OPTION (MERGE JOIN)") == "SELECT * FROM t"

    def test_strips_option_loop_join(self):
        assert _strip_option_clause("SELECT * FROM t OPTION (LOOP JOIN)") == "SELECT * FROM t"

    def test_no_option_unchanged(self):
        sql = "SELECT * FROM orders WHERE id = 1"
        assert _strip_option_clause(sql) == sql

    def test_case_insensitive(self):
        assert _strip_option_clause("SELECT 1 option (recompile)") == "SELECT 1"

    def test_option_in_string_literal_not_stripped(self):
        sql = "SELECT 'OPTION (RECOMPILE)' FROM t"
        assert _strip_option_clause(sql) == sql
