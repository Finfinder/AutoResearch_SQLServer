# tests/test_validator.py
from datetime import datetime
import pytest
from unittest.mock import MagicMock

from validator import (
    STRICT_LOB_MAX_BYTES,
    ValidationResult,
    _normalize_value,
    _strip_option_clause,
    build_strict_validation_context,
    get_row_count,
    validate_query_results,
    validate_row_count,
)


def _make_conn(row_value=None, raise_exc=None):
    cursor = MagicMock()
    if raise_exc is not None:
        cursor.execute.side_effect = raise_exc
    else:
        cursor.fetchone.return_value = (row_value,)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _make_row_conn(rows=None, description=None):
    rows = rows or []
    cursor = MagicMock()
    cursor.description = description or []
    cursor.fetchall.return_value = rows
    cursor.fetchone.side_effect = [*rows, None]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _make_multi_cursor_conn(*cursor_specs):
    conn = MagicMock()
    cursors = []
    for spec in cursor_specs:
        cursor = MagicMock()
        if spec.get("raise_exc") is not None:
            cursor.execute.side_effect = spec["raise_exc"]
        cursor.description = spec.get("description", [])
        rows = spec.get("rows", [])
        cursor.fetchall.return_value = rows
        row_value = spec.get("row_value")
        if row_value is not None:
            cursor.fetchone.side_effect = [(row_value,), None]
        else:
            cursor.fetchone.side_effect = [*rows, None]
        cursors.append(cursor)
    conn.cursor.side_effect = cursors
    return conn, cursors


def _make_strict_query_conn(rows=None, description=None, sql_type_names=None, row_count=None, metadata_error=None):
    metadata_description = [
        ("column_ordinal", int, None, None, None, None, None),
        ("system_type_name", str, None, None, None, None, None),
    ]
    metadata_rows = []
    if sql_type_names is not None:
        metadata_rows = [(index + 1, sql_type_name) for index, sql_type_name in enumerate(sql_type_names)]

    cursor_specs = [
        {
            "description": metadata_description,
            "rows": metadata_rows,
            "raise_exc": metadata_error,
        },
        {
            "description": description or [],
            "rows": rows or [],
        },
    ]
    if row_count is not None:
        cursor_specs.append({"row_value": row_count})
    return _make_multi_cursor_conn(*cursor_specs)


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

    def test_strips_top_level_order_by_before_count(self):
        conn, cursor = _make_conn(row_value=5)
        get_row_count("SELECT id FROM t ORDER BY id", conn)

        call_sql = cursor.execute.call_args[0][0]
        assert "COUNT(*)" in call_sql
        assert "ORDER BY" not in call_sql.upper()

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


class TestStrictValidationContext:
    def test_context_detects_explicit_order_by(self):
        conn, _ = _make_strict_query_conn(
            rows=[(1,), (2,)],
            description=[("id", int, None, None, None, None, None)],
            sql_type_names=["int"],
        )

        context = build_strict_validation_context("SELECT id FROM t ORDER BY id", conn)

        assert context["ordered"] is True
        assert context["base_signature"]
        assert context["base_row_count"] == 2
        assert context["fallback_reason"] is None

    def test_context_falls_back_when_order_by_analysis_fails(self):
        conn, _ = _make_row_conn(rows=[(1,)])

        context = build_strict_validation_context("SELECT FROM", conn)

        assert context["base_signature"] is None
        assert "Could not analyze ORDER BY" in context["fallback_reason"]

    def test_context_uses_result_set_metadata_to_detect_legacy_sql_types(self):
        conn, _ = _make_strict_query_conn(
            rows=[("legacy",)],
            description=[("payload", str, None, None, None, None, None)],
            sql_type_names=["text"],
        )

        context = build_strict_validation_context("SELECT payload FROM t", conn)

        assert context["base_signature"] is None
        assert "unsupported SQL type" in context["fallback_reason"]

    def test_context_falls_back_when_metadata_lookup_fails(self):
        conn, _ = _make_strict_query_conn(
            rows=[("legacy",)],
            description=[("payload", str, None, None, None, None, None)],
            metadata_error=RuntimeError("metadata unavailable"),
        )

        context = build_strict_validation_context("SELECT payload FROM t", conn)

        assert context["base_signature"] is None
        assert "metadata lookup failed" in context["fallback_reason"]

    def test_context_falls_back_for_sql_variant_type(self):
        conn, _ = _make_strict_query_conn(
            rows=[("legacy",)],
            description=[("payload", str, None, None, None, None, None)],
            sql_type_names=["sql_variant"],
        )

        context = build_strict_validation_context("SELECT payload FROM t", conn)

        assert context["base_signature"] is None
        assert "unsupported SQL type" in context["fallback_reason"]


class TestValidateQueryResults:
    def test_unordered_strict_validation_ignores_row_order(self):
        base_conn, _ = _make_strict_query_conn(
            rows=[(1,), (2,)],
            description=[("id", int, None, None, None, None, None)],
            sql_type_names=["int"],
        )
        strict_context = build_strict_validation_context("SELECT id FROM t", base_conn)
        variant_conn, _ = _make_strict_query_conn(
            rows=[(2,), (1,)],
            description=[("id", int, None, None, None, None, None)],
            sql_type_names=["int"],
        )

        result = validate_query_results(
            2,
            "SELECT id FROM t",
            variant_conn,
            strict_requested=True,
            strict_source="auto",
            strict_context=strict_context,
        )

        assert result.is_valid is True
        assert result.mode == "strict_hash"
        assert result.ordered is False
        assert result.strict_applied is True
        assert result.strict_source == "auto"

    def test_ordered_strict_validation_requires_same_sequence(self):
        base_conn, _ = _make_strict_query_conn(
            rows=[(1,), (2,)],
            description=[("id", int, None, None, None, None, None)],
            sql_type_names=["int"],
        )
        strict_context = build_strict_validation_context("SELECT id FROM t ORDER BY id", base_conn)
        variant_conn, _ = _make_strict_query_conn(
            rows=[(2,), (1,)],
            description=[("id", int, None, None, None, None, None)],
            sql_type_names=["int"],
        )

        result = validate_query_results(
            2,
            "SELECT id FROM t ORDER BY id",
            variant_conn,
            strict_requested=True,
            strict_source="cli",
            strict_context=strict_context,
        )

        assert result.is_valid is False
        assert result.mode == "strict_hash"
        assert result.ordered is True
        assert "Strict hash mismatch" in result.message

    def test_strict_validation_detects_same_count_but_different_rows(self):
        base_conn, _ = _make_strict_query_conn(
            rows=[(1, "a"), (2, "b")],
            description=[
                ("id", int, None, None, None, None, None),
                ("name", str, None, None, None, None, None),
            ],
            sql_type_names=["int", "nvarchar(20)"],
        )
        strict_context = build_strict_validation_context("SELECT id, name FROM t", base_conn)
        variant_conn, _ = _make_strict_query_conn(
            rows=[(1, "a"), (2, "c")],
            description=[
                ("id", int, None, None, None, None, None),
                ("name", str, None, None, None, None, None),
            ],
            sql_type_names=["int", "nvarchar(20)"],
        )

        result = validate_query_results(
            2,
            "SELECT id, name FROM t",
            variant_conn,
            strict_requested=True,
            strict_source="cli",
            strict_context=strict_context,
        )

        assert result.is_valid is False
        assert result.variant_count == 2
        assert result.mode == "strict_hash"
        assert "Strict hash mismatch" in result.message

    def test_strict_validation_falls_back_for_unsupported_text_type(self):
        base_conn, _ = _make_strict_query_conn(
            rows=[("ok",)],
            description=[("payload", str, None, None, None, None, None)],
            sql_type_names=["nvarchar(10)"],
        )
        strict_context = build_strict_validation_context("SELECT payload FROM t", base_conn)
        variant_conn, _ = _make_strict_query_conn(
            rows=[("legacy",)],
            description=[("payload", str, None, None, None, None, None)],
            sql_type_names=["text"],
            row_count=1,
        )

        result = validate_query_results(
            1,
            "SELECT payload FROM t",
            variant_conn,
            strict_requested=True,
            strict_source="cli",
            strict_context=strict_context,
        )

        assert result.is_valid is True
        assert result.mode == "row_count"
        assert result.strict_requested is True
        assert result.strict_applied is False
        assert result.fallback_reason is not None
        assert "unsupported SQL type" in result.fallback_reason

    def test_strict_validation_returns_metadata_when_base_count_is_unavailable(self):
        conn, _ = _make_row_conn(rows=[("ignored",)], description=[("payload", str, None, None, None, None, None)])

        result = validate_query_results(
            None,
            "SELECT payload FROM t",
            conn,
            strict_requested=True,
            strict_source="cli",
            strict_context={
                "ordered": False,
                "base_signature": None,
                "base_row_count": None,
                "fallback_reason": "Strict validation context is unavailable",
                "warnings": ["Strict validation setup failed"],
            },
        )

        assert result.is_valid is True
        assert result.base_count is None
        assert result.variant_count == -1
        assert result.mode == "row_count"
        assert result.strict_requested is True
        assert result.strict_applied is False
        assert result.fallback_reason == "Strict validation context is unavailable"
        assert "base row count unavailable" in result.message

    def test_strict_validation_falls_back_for_large_lob(self):
        base_conn, _ = _make_strict_query_conn(
            rows=[("small",)],
            description=[("payload", str, None, None, None, None, None)],
            sql_type_names=["nvarchar(max)"],
        )
        strict_context = build_strict_validation_context("SELECT payload FROM t", base_conn)
        large_value = "x" * (STRICT_LOB_MAX_BYTES + 1)
        variant_conn, _ = _make_strict_query_conn(
            rows=[(large_value,)],
            description=[("payload", str, None, None, None, None, None)],
            sql_type_names=["nvarchar(max)"],
            row_count=1,
        )

        result = validate_query_results(
            1,
            "SELECT payload FROM t",
            variant_conn,
            strict_requested=True,
            strict_source="auto",
            strict_context=strict_context,
        )

        assert result.is_valid is True
        assert result.mode == "row_count"
        assert result.strict_requested is True
        assert result.strict_source == "auto"
        assert "LOB value" in result.fallback_reason

    def test_strict_validation_supports_datetime_rows(self):
        timestamp = datetime(2024, 1, 2, 3, 4, 5)
        base_conn, _ = _make_strict_query_conn(
            rows=[(timestamp,)],
            description=[("created_at", datetime, None, None, None, None, None)],
            sql_type_names=["datetime2"],
        )
        strict_context = build_strict_validation_context("SELECT created_at FROM t", base_conn)
        variant_conn, _ = _make_strict_query_conn(
            rows=[(timestamp,)],
            description=[("created_at", datetime, None, None, None, None, None)],
            sql_type_names=["datetime2"],
        )

        result = validate_query_results(
            1,
            "SELECT created_at FROM t",
            variant_conn,
            strict_requested=True,
            strict_source="auto",
            strict_context=strict_context,
        )

        assert result.is_valid is True
        assert result.mode == "strict_hash"

    def test_float_normalization_uses_lossless_representation(self):
        normalized = _normalize_value(0.1)

        assert normalized == ["float", format(0.1, ".17g")]

    def test_strict_validation_supports_xml_text_serialization(self):
        base_conn, _ = _make_strict_query_conn(
            rows=[("<root><item>1</item></root>",)],
            description=[("payload", str, None, None, None, None, None)],
            sql_type_names=["xml"],
        )
        strict_context = build_strict_validation_context("SELECT payload FROM t", base_conn)
        variant_conn, _ = _make_strict_query_conn(
            rows=[("<root><item>1</item></root>",)],
            description=[("payload", str, None, None, None, None, None)],
            sql_type_names=["xml"],
        )

        result = validate_query_results(
            1,
            "SELECT payload FROM t",
            variant_conn,
            strict_requested=True,
            strict_source="cli",
            strict_context=strict_context,
        )

        assert result.is_valid is True
        assert result.mode == "strict_hash"
