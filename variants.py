# variants.py

def generate_variants(base_query):
    variants = []

    # 1. JOIN → EXISTS
    variants.append(base_query.replace(
        "JOIN [Sales].[Customer] c ON o.[CustomerID] = c.[CustomerID]\nWHERE",
        "WHERE EXISTS (SELECT 1 FROM [Sales].[Customer] c WHERE c.[CustomerID] = o.[CustomerID])\nAND"
    ))

    # 2. Dodanie TOP (symulacja ograniczenia)
    variants.append(base_query.replace(
        "SELECT",
        "SELECT TOP 1000"
    ))

    # 3. Hint NOLOCK (ryzykowny, ale testowo)
    variants.append(base_query.replace(
        "[Sales].[SalesOrderHeader] o",
        "[Sales].[SalesOrderHeader] o WITH (NOLOCK)"
    ))

    # 4. Zmiana kolejności WHERE (czasem planner reaguje)
    variants.append(base_query + " OPTION (RECOMPILE)")

    return variants