# Guardrails

AutoResearch generates SQL query variants to benchmark structural alternatives. A guardrail is a safety rule that prevents a variant from running if it would produce semantically non-equivalent results — because benchmarking such a variant would be misleading.

---

## DO NOT — Rules that block a variant

The following transformations are **blocked** before the variant reaches the benchmark. A blocked variant is logged and skipped; it never executes against the database.

### G1 — No LIMIT / TOP added

**Rule**: A variant must not add `TOP N` or `LIMIT` when the base query has none.

**Why**: Adding `TOP N` reduces the result set. A query that processes 100 rows is not faster than one that processes 10 000 rows in the same scenario — it is a different query. Such a comparison has no diagnostic value and will always favour the limited variant.

**Example blocked**: `SELECT TOP 1000 * FROM Orders WHERE ...` vs base `SELECT * FROM Orders WHERE ...`

---

### G2 — No WHERE clause removal

**Rule**: If the base query has a `WHERE` clause, the variant must also have a `WHERE` clause at the top level (or be a `UNION`-type expression where WHERE is distributed into branches).

**Why**: Removing a filter means the variant returns more rows than the base. It is not a structural optimisation — it is a different query with different results.

**Example blocked**: `SELECT * FROM Orders` vs base `SELECT * FROM Orders WHERE OrderDate > '2024-01-01'`

**Exception**: `OR→UNION ALL` variants are exempt. This transformation distributes the WHERE predicate into each UNION branch, so the top-level expression has no WHERE node, but the semantics are preserved.

---

## WARNINGS — Rules that allow a variant but flag it

These violations are recorded in `results.json` under `guardrail_warnings` and printed to the console. The variant is still benchmarked.

### G4 — NOLOCK hint

**Rule**: A variant using `WITH (NOLOCK)` will produce a warning.

**Why**: NOLOCK (READ UNCOMMITTED) may return dirty reads — rows from transactions that have not yet been committed or have been rolled back. The variant may appear faster because it skips shared lock acquisition, but the results may be inconsistent. This is a valid query hint for read-heavy reporting workloads, but the user should be aware of the trade-off.

---

## Validation

Beyond static guardrails (AST checks), the tool performs a **runtime validation** before each variant is benchmarked:

### Row count validation

The tool runs `SELECT COUNT(*) FROM (<variant_query>) AS _v` and compares it against the base query row count. If the counts differ, the variant is blocked.

This catches semantic changes that are invisible at the AST level, such as:
- `OR→UNION ALL` producing duplicate rows for overlapping OR conditions
- JOIN rewrites that accidentally multiply rows (Cartesian product artefacts)

**Graceful degradation**: If the `COUNT(*)` query fails (timeout, permission error, network issue), the variant is **not blocked** — a warning is printed and the benchmark continues.

---

## Adding new transformations

When writing a new transform in `variants.py`, ask:

1. **Does it preserve the full result set?** If not — either fix the transform or do not add it.
2. **Does it add TOP/LIMIT?** → G1 will block it automatically.
3. **Does it remove the WHERE clause?** → G2 will block it automatically (unless it is a UNION variant).
4. **Can it produce duplicate rows?** → Runtime row count validation will catch it.

If a new transform intentionally changes semantics (e.g. sampling), it must not be added to `_TRANSFORMS` — it belongs in a separate, clearly named tool.
