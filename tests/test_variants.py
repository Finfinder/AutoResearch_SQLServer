# tests/test_variants.py
import os

import pytest
import sqlglot

from variants import VariantGenerationError, generate_variants

_JOIN_QUERY = (
    "SELECT o.* "
    "FROM [Sales].[SalesOrderHeader] AS o "
    "JOIN [Sales].[Customer] AS c ON o.[CustomerID] = c.[CustomerID] "
    "WHERE o.[OrderDate] > '2024-01-01'"
)

_SIMPLE_QUERY = "SELECT 1"

_TWO_JOIN_QUERY = (
    "SELECT o.* "
    "FROM [Sales].[SalesOrderHeader] AS o "
    "JOIN [Sales].[Customer] AS c ON o.[CustomerID] = c.[CustomerID] "
    "JOIN [Sales].[Address] AS a ON o.[ShipAddressID] = a.[AddressID] "
    "WHERE o.[OrderDate] > '2024-01-01'"
)

_IN_SUBQUERY_QUERY = (
    "SELECT p.* "
    "FROM [Production].[Product] AS p "
    "WHERE p.[ProductID] IN (SELECT [ProductID] FROM [Sales].[SalesOrderDetail])"
)

_OR_QUERY = (
    "SELECT * FROM [Sales].[SalesOrderHeader] AS o "
    "WHERE o.[Status] = 1 OR o.[Status] = 5"
)

_OR_NESTED_QUERY = (
    "SELECT * FROM [Sales].[SalesOrderHeader] AS o "
    "WHERE o.[TerritoryID] = 5 AND (o.[Status] = 1 OR o.[Status] = 5)"
)

_DISTINCT_QUERY = (
    "SELECT DISTINCT p.[Color] FROM [Production].[Product] AS p "
    "WHERE p.[Color] IS NOT NULL"
)

_SUBQUERY_FROM_QUERY = (
    "SELECT sub.[CustomerID] "
    "FROM (SELECT [CustomerID] FROM [Sales].[Customer] WHERE [TerritoryID] = 1) AS sub"
)

_JOIN_WITH_SUBQUERY_QUERY = (
    "SELECT o.* "
    "FROM [Sales].[SalesOrderHeader] AS o "
    "JOIN (SELECT [CustomerID], MAX([OrderDate]) AS MaxDate FROM [Sales].[SalesOrderHeader] GROUP BY [CustomerID]) AS sub "
    "ON o.[CustomerID] = sub.[CustomerID] AND o.[OrderDate] = sub.[MaxDate]"
)


class TestInterface:
    def test_returns_list_of_tuples(self):
        result = generate_variants(_JOIN_QUERY)
        assert isinstance(result, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in result)

    def test_labels_are_non_empty_strings(self):
        result = generate_variants(_JOIN_QUERY)
        assert all(isinstance(label, str) and label for label, _ in result)

    def test_sql_strings_are_non_empty(self):
        result = generate_variants(_JOIN_QUERY)
        assert all(isinstance(sql, str) and sql for _, sql in result)

    def test_variants_are_valid_sql(self):
        result = generate_variants(_JOIN_QUERY)
        for label, sql in result:
            if label == "Index suggestions":
                sql_body = "\n".join(
                    line for line in sql.splitlines() if not line.startswith("--")
                )
            else:
                sql_body = sql
            sqlglot.parse_one(sql_body, dialect="tsql")

    def test_simple_query_returns_variants(self):
        result = generate_variants(_SIMPLE_QUERY)
        assert len(result) > 0
        labels = [label for label, _ in result]
        assert "RECOMPILE" in labels

    def test_simple_query_no_join_variant(self):
        result = generate_variants(_SIMPLE_QUERY)
        labels = [label for label, _ in result]
        assert "JOIN→EXISTS" not in labels


class TestVariantGenerationError:
    def test_invalid_sql_raises_error(self):
        with pytest.raises(VariantGenerationError):
            generate_variants("THIS IS NOT SQL !!!")

    def test_error_is_exception_subclass(self):
        assert issubclass(VariantGenerationError, Exception)

    def test_error_has_message(self):
        try:
            generate_variants("SELECT FROM WHERE")
        except VariantGenerationError as e:
            assert str(e)

    def test_error_fields_exist(self):
        err = VariantGenerationError("msg", line=2, col=5, fragment="foo", suggestion="bar")
        assert err.line == 2
        assert err.col == 5
        assert err.fragment == "foo"
        assert err.suggestion == "bar"

    def test_error_fields_can_be_none(self):
        err = VariantGenerationError("msg")
        assert err.line is None
        assert err.col is None
        assert err.fragment is None
        assert err.suggestion is None


class TestMaxVariants:
    def test_max_variants_limits_output(self, monkeypatch):
        monkeypatch.setenv("MAX_VARIANTS", "2")
        result = generate_variants(_TWO_JOIN_QUERY)
        assert len(result) <= 2

    def test_max_variants_default_is_60(self, monkeypatch):
        monkeypatch.delenv("MAX_VARIANTS", raising=False)
        result = generate_variants(_TWO_JOIN_QUERY)
        assert len(result) <= 60


class TestJoinToExists:
    def test_produces_variant_for_join(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "JOIN→EXISTS" in labels

    def test_join_removed_from_output(self):
        result = generate_variants(_JOIN_QUERY)
        _, sql = next((l, s) for l, s in result if l == "JOIN→EXISTS")
        assert "JOIN [Sales].[Customer]" not in sql

    def test_exists_in_where(self):
        result = generate_variants(_JOIN_QUERY)
        _, sql = next((l, s) for l, s in result if l == "JOIN→EXISTS")
        assert "EXISTS" in sql

    def test_no_join_no_variant(self):
        result = generate_variants(_SIMPLE_QUERY)
        labels = [label for label, _ in result]
        assert "JOIN→EXISTS" not in labels

    def test_two_joins_produce_indexed_labels(self):
        result = generate_variants(_TWO_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "JOIN→EXISTS[1]" in labels
        assert "JOIN→EXISTS[2]" in labels


class TestNolock:
    def test_produces_nolock(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "NOLOCK" in labels

    def test_nolock_on_all_tables(self):
        result = generate_variants(_JOIN_QUERY)
        _, sql = next((l, s) for l, s in result if l == "NOLOCK")
        assert sql.count("NOLOCK") >= 2

    def test_no_nolock_for_already_nolock(self):
        sql = "SELECT * FROM [Sales].[SalesOrderHeader] WITH (NOLOCK)"
        result = generate_variants(sql)
        labels = [label for label, _ in result]
        assert "NOLOCK" not in labels


class TestRecompile:
    def test_produces_recompile(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "RECOMPILE" in labels

    def test_recompile_in_sql(self):
        result = generate_variants(_JOIN_QUERY)
        _, sql = next((l, s) for l, s in result if l == "RECOMPILE")
        assert "RECOMPILE" in sql

    def test_no_duplicate_recompile(self):
        sql = "SELECT * FROM [Sales].[SalesOrderHeader] OPTION (RECOMPILE)"
        result = generate_variants(sql)
        labels = [label for label, _ in result]
        assert "RECOMPILE" not in labels


class TestInToExists:
    def test_produces_in_to_exists(self):
        result = generate_variants(_IN_SUBQUERY_QUERY)
        labels = [label for label, _ in result]
        assert "IN→EXISTS" in labels

    def test_in_not_in_output(self):
        result = generate_variants(_IN_SUBQUERY_QUERY)
        _, sql = next((l, s) for l, s in result if l == "IN→EXISTS")
        assert " IN " not in sql.upper() and "EXISTS" in sql

    def test_no_in_no_variant(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "IN→EXISTS" not in labels


class TestOrToUnion:
    def test_produces_union_all(self):
        result = generate_variants(_OR_QUERY)
        labels = [label for label, _ in result]
        assert "OR→UNION ALL" in labels

    def test_union_all_in_sql(self):
        result = generate_variants(_OR_QUERY)
        _, sql = next((l, s) for l, s in result if l == "OR→UNION ALL")
        assert "UNION ALL" in sql

    def test_no_or_no_variant(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "OR→UNION ALL" not in labels

    def test_nested_or_preserves_and_condition(self):
        result = generate_variants(_OR_NESTED_QUERY)
        labels = [label for label, _ in result]
        assert "OR→UNION ALL" in labels
        parts = [sql for label, sql in result if label == "OR→UNION ALL"]
        assert len(parts) == 1
        combined = parts[0]
        assert combined.count("TerritoryID") >= 2


class TestDistinctToGroupBy:
    def test_produces_group_by(self):
        result = generate_variants(_DISTINCT_QUERY)
        labels = [label for label, _ in result]
        assert "DISTINCT→GROUP BY" in labels

    def test_distinct_removed(self):
        result = generate_variants(_DISTINCT_QUERY)
        _, sql = next((l, s) for l, s in result if l == "DISTINCT→GROUP BY")
        assert "DISTINCT" not in sql

    def test_group_by_present(self):
        result = generate_variants(_DISTINCT_QUERY)
        _, sql = next((l, s) for l, s in result if l == "DISTINCT→GROUP BY")
        assert "GROUP BY" in sql

    def test_no_distinct_no_variant(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "DISTINCT→GROUP BY" not in labels


class TestSubqueryToCte:
    def test_produces_cte(self):
        result = generate_variants(_SUBQUERY_FROM_QUERY)
        labels = [label for label, _ in result]
        assert "Subquery→CTE" in labels

    def test_with_keyword_in_sql(self):
        result = generate_variants(_SUBQUERY_FROM_QUERY)
        _, sql = next((l, s) for l, s in result if l == "Subquery→CTE")
        assert sql.startswith("WITH")

    def test_no_subquery_no_variant(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "Subquery→CTE" not in labels


class TestJoinReorder:
    def test_produces_reorder_for_two_joins(self):
        result = generate_variants(_TWO_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "JOIN reorder" in labels

    def test_no_reorder_for_single_join(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "JOIN reorder" not in labels


class TestCrossApply:
    def test_produces_cross_apply(self):
        result = generate_variants(_JOIN_WITH_SUBQUERY_QUERY)
        labels = [label for label, _ in result]
        assert "CROSS APPLY" in labels

    def test_cross_apply_in_sql(self):
        result = generate_variants(_JOIN_WITH_SUBQUERY_QUERY)
        _, sql = next((l, s) for l, s in result if l == "CROSS APPLY")
        assert "CROSS APPLY" in sql

    def test_no_cross_apply_for_simple_join(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "CROSS APPLY" not in labels


class TestJoinHints:
    def test_produces_hash_merge_loop(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "HASH JOIN" in labels
        assert "MERGE JOIN" in labels
        assert "LOOP JOIN" in labels

    def test_hint_in_sql(self):
        result = generate_variants(_JOIN_QUERY)
        _, sql = next((l, s) for l, s in result if l == "HASH JOIN")
        assert "HASH" in sql

    def test_no_hints_for_no_join(self):
        result = generate_variants(_SIMPLE_QUERY)
        labels = [label for label, _ in result]
        assert "HASH JOIN" not in labels
        assert "MERGE JOIN" not in labels
        assert "LOOP JOIN" not in labels


class TestIndexSuggestions:
    def test_produces_index_suggestions(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "Index suggestions" in labels

    def test_comment_lines_in_sql(self):
        result = generate_variants(_JOIN_QUERY)
        _, sql = next((l, s) for l, s in result if l == "Index suggestions")
        assert "-- Consider index on" in sql

    def test_no_suggestions_for_parameterless_query(self):
        result = generate_variants("SELECT 1 AS x")
        labels = [label for label, _ in result]
        assert "Index suggestions" not in labels


class TestComposedVariants:
    def test_composed_variants_generated(self):
        result = generate_variants(_JOIN_QUERY)
        composed = [label for label, _ in result if " + " in label]
        assert len(composed) >= 1

    def test_composed_label_format(self):
        result = generate_variants(_JOIN_QUERY)
        composed_labels = [label for label, _ in result if " + " in label]
        for label in composed_labels:
            assert label.count(" + ") == 1

    def test_nolock_recompile_pair_exists(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "NOLOCK + RECOMPILE" in labels

    def test_join_exists_nolock_pair_exists(self):
        result = generate_variants(_JOIN_QUERY)
        labels = [label for label, _ in result]
        assert "JOIN→EXISTS + NOLOCK" in labels

    def test_or_union_not_in_composed_labels(self):
        result = generate_variants(_JOIN_QUERY)
        composed_labels = [label for label, _ in result if " + " in label]
        assert not any("OR→UNION ALL" in label for label in composed_labels)

    def test_index_suggestions_not_in_composed_labels(self):
        result = generate_variants(_JOIN_QUERY)
        composed_labels = [label for label, _ in result if " + " in label]
        assert not any("Index suggestions" in label for label in composed_labels)

    def test_composed_variants_are_valid_sql(self):
        result = generate_variants(_JOIN_QUERY)
        composed = [(label, sql) for label, sql in result if " + " in label]
        for label, sql in composed:
            parsed = sqlglot.parse_one(sql, dialect="tsql")
            assert parsed is not None, f"Composed variant '{label}' is not valid SQL"

    def test_composed_variants_respect_max_variants(self, monkeypatch):
        monkeypatch.setenv("MAX_VARIANTS", "2")
        result = generate_variants(_JOIN_QUERY)
        assert len(result) <= 2

    def test_simple_query_no_composed_pairs(self):
        result = generate_variants("SELECT 1")
        composed = [label for label, _ in result if " + " in label]
        assert len(composed) == 0
