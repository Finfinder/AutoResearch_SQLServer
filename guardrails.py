# guardrails.py
import re
from dataclasses import dataclass

import sqlglot
import sqlglot.expressions as exp


@dataclass
class Violation:
    rule_id: str
    severity: str
    message: str


def _strip_sql_comments(sql):
    # Remove single-line comments (-- ...) before parsing
    return re.sub(r"--[^\n]*", "", sql)


def _has_limit_or_top(ast):
    if ast is None:
        return False
    if isinstance(ast, exp.Select):
        return ast.args.get("limit") is not None
    # For UNION/other top-level expressions, check direct Select children only
    # (exclude subquery Selects whose parent is exp.Subquery)
    return any(
        isinstance(node, exp.Select) and node.args.get("limit") is not None
        for node in ast.walk()
        if node.parent is None or isinstance(node.parent, exp.Union)
    )


def _has_top_level_where(ast):
    if ast is None:
        return False
    if isinstance(ast, exp.Select):
        return ast.args.get("where") is not None
    return False


def _is_union(ast):
    return isinstance(ast, (exp.Union,))


def _hint_is_nolock(hint):
    if not isinstance(hint, exp.WithTableHint):
        return False
    return any(
        isinstance(v, exp.Var) and v.name.upper() == "NOLOCK"
        for v in hint.args.get("expressions", [])
    )


def _has_nolock_hint(ast):
    for tbl in ast.find_all(exp.Table):
        if any(_hint_is_nolock(h) for h in tbl.args.get("hints") or []):
            return True
    return False


def check_variant(base_sql, variant_sql, variant_label):
    violations = []

    try:
        base_ast = sqlglot.parse_one(base_sql, dialect="tsql")
    except Exception:
        return violations

    cleaned_variant = _strip_sql_comments(variant_sql)
    try:
        variant_ast = sqlglot.parse_one(cleaned_variant, dialect="tsql")
    except Exception:
        return violations

    # G1: no_limit_added — variant adds TOP/LIMIT that base doesn't have
    base_has_limit = _has_limit_or_top(base_ast)
    variant_has_limit = _has_limit_or_top(variant_ast)
    if variant_has_limit and not base_has_limit:
        violations.append(Violation(
            rule_id="G1",
            severity="block",
            message=f"[{variant_label}] adds TOP/LIMIT which is not present in the base query — variant would reduce the result set.",
        ))

    # G2: no_where_removed — base has WHERE, variant has no WHERE at top level (and is not UNION)
    base_has_where = _has_top_level_where(base_ast)
    variant_has_where = _has_top_level_where(variant_ast)
    if base_has_where and not variant_has_where and not _is_union(variant_ast):
        violations.append(Violation(
            rule_id="G2",
            severity="block",
            message=f"[{variant_label}] removes the WHERE clause that is present in the base query — variant may return more rows.",
        ))

    # G4: nolock_warning — variant uses NOLOCK hint
    if _has_nolock_hint(variant_ast):
        violations.append(Violation(
            rule_id="G4",
            severity="warn",
            message=f"[{variant_label}] uses NOLOCK hint — results may include uncommitted (dirty) reads.",
        ))

    return violations
