# tests/test_guardrails.py
from guardrails import Violation, check_variant

_BASE_WHERE = (
    "SELECT o.* FROM [Sales].[SalesOrderHeader] AS o "
    "WHERE o.[OrderDate] > '2024-01-01'"
)
_BASE_NO_WHERE = "SELECT * FROM [Sales].[SalesOrderHeader]"
_BASE_NO_TOP = "SELECT * FROM [Sales].[SalesOrderHeader] WHERE [Status] = 1"
_BASE_WITH_TOP = "SELECT TOP 500 * FROM [Sales].[SalesOrderHeader] WHERE [Status] = 1"


class TestNoLimitAdded:
    def test_variant_adds_top_base_without_top_is_blocked(self):
        variant = "SELECT TOP 1000 * FROM [Sales].[SalesOrderHeader] WHERE [Status] = 1"
        violations = check_variant(_BASE_NO_TOP, variant, "TOP 1000")
        block = [v for v in violations if v.rule_id == "G1" and v.severity == "block"]
        assert len(block) == 1

    def test_both_have_top_no_violation(self):
        variant = "SELECT TOP 100 * FROM [Sales].[SalesOrderHeader] WHERE [Status] = 1"
        violations = check_variant(_BASE_WITH_TOP, variant, "TOP 100")
        g1 = [v for v in violations if v.rule_id == "G1"]
        assert g1 == []

    def test_neither_has_top_no_violation(self):
        variant = "SELECT * FROM [Sales].[SalesOrderHeader] WHERE [Status] = 1"
        violations = check_variant(_BASE_NO_TOP, variant, "no-top")
        g1 = [v for v in violations if v.rule_id == "G1"]
        assert g1 == []


class TestNoWhereRemoved:
    def test_base_has_where_variant_has_no_where_is_blocked(self):
        variant = "SELECT o.* FROM [Sales].[SalesOrderHeader] AS o"
        violations = check_variant(_BASE_WHERE, variant, "no-where")
        block = [v for v in violations if v.rule_id == "G2" and v.severity == "block"]
        assert len(block) == 1

    def test_both_have_where_no_violation(self):
        variant = "SELECT o.* FROM [Sales].[SalesOrderHeader] AS o WHERE o.[OrderDate] > '2023-01-01'"
        violations = check_variant(_BASE_WHERE, variant, "diff-date")
        g2 = [v for v in violations if v.rule_id == "G2"]
        assert g2 == []

    def test_neither_has_where_no_violation(self):
        variant = "SELECT * FROM [Sales].[SalesOrderHeader]"
        violations = check_variant(_BASE_NO_WHERE, variant, "no-where-base")
        g2 = [v for v in violations if v.rule_id == "G2"]
        assert g2 == []

    def test_union_variant_no_g2_even_without_top_level_where(self):
        # OR→UNION ALL moves WHERE into branches — top-level has no WHERE but is a Union node
        union_variant = (
            "SELECT * FROM [Sales].[SalesOrderHeader] AS o WHERE o.[Status] = 1 "
            "UNION ALL "
            "SELECT * FROM [Sales].[SalesOrderHeader] AS o WHERE o.[Status] = 5"
        )
        base_or = (
            "SELECT * FROM [Sales].[SalesOrderHeader] AS o "
            "WHERE o.[Status] = 1 OR o.[Status] = 5"
        )
        violations = check_variant(base_or, union_variant, "OR→UNION ALL")
        g2 = [v for v in violations if v.rule_id == "G2"]
        assert g2 == []


    def test_subquery_to_cte_variant_no_g2_false_positive(self):
        # CTE variant keeps WHERE inside the outer SELECT — G2 must not fire
        cte_variant = (
            "WITH cte_1 AS (SELECT * FROM [Sales].[SalesOrderHeader]) "
            "SELECT o.* FROM cte_1 AS o WHERE o.[OrderDate] > '2024-01-01'"
        )
        violations = check_variant(_BASE_WHERE, cte_variant, "Subquery\u2192CTE")
        g2 = [v for v in violations if v.rule_id == "G2"]
        assert g2 == []


class TestNolockWarning:
    def test_variant_with_nolock_produces_warn(self):
        variant = "SELECT * FROM [Sales].[SalesOrderHeader] WITH (NOLOCK) WHERE [Status] = 1"
        violations = check_variant(_BASE_NO_TOP, variant, "NOLOCK")
        warn = [v for v in violations if v.rule_id == "G4" and v.severity == "warn"]
        assert len(warn) == 1

    def test_variant_without_nolock_no_violation(self):
        variant = "SELECT * FROM [Sales].[SalesOrderHeader] WHERE [Status] = 1"
        violations = check_variant(_BASE_NO_TOP, variant, "clean")
        g4 = [v for v in violations if v.rule_id == "G4"]
        assert g4 == []


class TestMultipleViolations:
    def test_top_and_nolock_both_detected(self):
        variant = "SELECT TOP 1000 * FROM [Sales].[SalesOrderHeader] WITH (NOLOCK) WHERE [Status] = 1"
        violations = check_variant(_BASE_NO_TOP, variant, "TOP+NOLOCK")
        rule_ids = [v.rule_id for v in violations]
        assert "G1" in rule_ids
        assert "G4" in rule_ids

    def test_violation_message_contains_label(self):
        variant = "SELECT TOP 1000 * FROM [Sales].[SalesOrderHeader] WHERE [Status] = 1"
        violations = check_variant(_BASE_NO_TOP, variant, "MyLabel")
        assert any("MyLabel" in v.message for v in violations)


class TestEdgeCases:
    def test_unparseable_variant_returns_empty_list(self):
        violations = check_variant(_BASE_NO_TOP, "THIS IS NOT SQL !!!", "broken")
        assert violations == []

    def test_unparseable_base_returns_empty_list(self):
        violations = check_variant("NOT SQL !!!", _BASE_NO_TOP, "ok-variant")
        assert violations == []

    def test_index_suggestions_comment_variant_no_false_positive(self):
        # Comment-prefixed variant (index suggestions) should be handled gracefully
        variant = (
            "-- Consider index on [Sales].[SalesOrderHeader]([Status])\n"
            "SELECT * FROM [Sales].[SalesOrderHeader] WHERE [Status] = 1"
        )
        violations = check_variant(_BASE_NO_TOP, variant, "Index suggestions")
        # No block violations expected — G4 not applicable, no TOP, WHERE preserved
        block = [v for v in violations if v.severity == "block"]
        assert block == []
