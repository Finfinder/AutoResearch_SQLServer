# tests/test_variants.py
from variants import generate_variants

_BASE_QUERY = (
    "-- query.sql\n"
    "SELECT o.*\n"
    "FROM [Sales].[SalesOrderHeader] o\n"
    "JOIN [Sales].[Customer] c ON o.[CustomerID] = c.[CustomerID]\n"
    "WHERE o.[OrderDate] > '2024-01-01'"
)

_SIMPLE_QUERY = "SELECT 1"


class TestGenerateVariants:
    def test_returns_four_variants(self):
        result = generate_variants(_BASE_QUERY)
        assert len(result) == 4

    def test_variant_join_to_exists(self):
        result = generate_variants(_BASE_QUERY)
        variant = result[0]
        assert "WHERE EXISTS" in variant
        assert "SELECT 1 FROM [Sales].[Customer] c WHERE c.[CustomerID] = o.[CustomerID]" in variant
        assert "JOIN [Sales].[Customer]" not in variant

    def test_variant_top(self):
        result = generate_variants(_BASE_QUERY)
        variant = result[1]
        assert "SELECT TOP 1000" in variant

    def test_variant_nolock(self):
        result = generate_variants(_BASE_QUERY)
        variant = result[2]
        assert "WITH (NOLOCK)" in variant
        assert "[Sales].[SalesOrderHeader] o WITH (NOLOCK)" in variant

    def test_variant_recompile(self):
        result = generate_variants(_BASE_QUERY)
        variant = result[3]
        assert variant.endswith("OPTION (RECOMPILE)")

    def test_no_match_returns_original_for_string_transforms(self):
        result = generate_variants(_SIMPLE_QUERY)
        assert len(result) == 4
        # Warianty 0-2: str.replace nie znajduje wzorca — zwraca oryginał
        assert result[0] == _SIMPLE_QUERY
        assert result[1] == _SIMPLE_QUERY.replace("SELECT", "SELECT TOP 1000")
        assert result[2] == _SIMPLE_QUERY
        # Wariant 3: zawsze dopisuje OPTION (RECOMPILE)
        assert result[3] == _SIMPLE_QUERY + " OPTION (RECOMPILE)"
