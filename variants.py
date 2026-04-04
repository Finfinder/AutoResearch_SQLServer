# variants.py

def generate_variants(base_query):
    variants = []

    # 1. JOIN → EXISTS
    variants.append(base_query.replace(
        "JOIN customers c ON o.customer_id = c.id",
        "WHERE EXISTS (SELECT 1 FROM customers c WHERE c.id = o.customer_id)"
    ))

    # 2. Dodanie TOP (symulacja ograniczenia)
    variants.append(base_query.replace(
        "SELECT",
        "SELECT TOP 1000"
    ))

    # 3. Hint NOLOCK (ryzykowny, ale testowo)
    variants.append(base_query.replace(
        "orders o",
        "orders o WITH (NOLOCK)"
    ))

    # 4. Zmiana kolejności WHERE (czasem planner reaguje)
    variants.append(base_query + " OPTION (RECOMPILE)")

    return variants