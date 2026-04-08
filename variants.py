# variants.py
import os
import sys
import sqlglot
import sqlglot.expressions as exp


class VariantGenerationError(Exception):
    def __init__(self, message, line=None, col=None, fragment=None, suggestion=None):
        super().__init__(message)
        self.line = line
        self.col = col
        self.fragment = fragment
        self.suggestion = suggestion

    def __str__(self):
        parts = [super().__str__()]
        if self.line is not None and self.col is not None:
            parts.append(f"Position: line {self.line}, column {self.col}")
        if self.fragment:
            parts.append(f"Fragment: {self.fragment}")
        if self.suggestion:
            parts.append(f"Suggestion: {self.suggestion}")
        return "\n".join(parts)


def _to_sql(ast):
    return ast.sql(dialect="tsql")


def _is_inner_join(join_node):
    kind = join_node.args.get("kind")
    if kind is None:
        return True
    kind_str = str(kind).upper() if kind else ""
    return kind_str in ("", "INNER", "JOIN")


def _attach_exists_to_where(ast_c, exists_expr):
    where_node = ast_c.args.get("where")
    if where_node:
        new_cond = exp.And(this=where_node.this.copy(), expression=exists_expr)
        ast_c.set("where", exp.Where(this=new_cond))
    else:
        ast_c.set("where", exp.Where(this=exists_expr))


def _transform_join_to_exists(ast):
    if not isinstance(ast, exp.Select):
        return []
    top_joins = ast.args.get("joins") or []
    inner_joins = [j for j in top_joins if _is_inner_join(j) and j.args.get("on") is not None]
    if not inner_joins:
        return []
    results = []
    for i, join_node in enumerate(inner_joins):
        on_cond = join_node.args.get("on")
        joined_table = join_node.args.get("this")
        if on_cond is None or joined_table is None:
            continue

        ast_c = ast.copy()
        top_joins_c = ast_c.args.get("joins") or []
        inner_joins_c = [j for j in top_joins_c if _is_inner_join(j) and j.args.get("on") is not None]
        if i >= len(inner_joins_c):
            continue
        target_join = inner_joins_c[i]
        target_on = target_join.args.get("on").copy()
        target_table = target_join.args.get("this").copy()

        exists_select = exp.select(exp.Literal.number(1)).from_(target_table)
        exists_select.set("where", exp.Where(this=target_on))
        exists_expr = exp.Exists(this=exists_select)

        remaining_joins = [j for j in top_joins_c if j is not target_join]
        ast_c.set("joins", remaining_joins)

        _attach_exists_to_where(ast_c, exists_expr)

        label = "JOIN\u2192EXISTS" if len(inner_joins) == 1 else f"JOIN\u2192EXISTS[{i+1}]"
        results.append((label, ast_c))
    return results


def _transform_top_n(ast, n=1000):
    if not isinstance(ast, exp.Select):
        return []
    if ast.args.get("limit") is not None:
        return []
    ast_c = ast.copy()
    ast_c.set("limit", exp.Limit(expression=exp.Literal.number(n)))
    return [(f"TOP {n}", ast_c)]


def _transform_nolock(ast):
    tables = list(ast.find_all(exp.Table))
    if not tables:
        return []
    already_nolock = all(
        any(
            isinstance(h, exp.WithTableHint)
            and any(
                isinstance(v, exp.Var) and v.name.upper() == "NOLOCK"
                for v in h.args.get("expressions", [])
            )
            for h in (t.args.get("hints") or [])
        )
        for t in tables
    )
    if already_nolock:
        return []
    ast_c = ast.copy()
    for tbl in ast_c.find_all(exp.Table):
        existing_hints = tbl.args.get("hints") or []
        has_nolock = any(
            isinstance(h, exp.WithTableHint)
            and any(
                isinstance(v, exp.Var) and v.name.upper() == "NOLOCK"
                for v in h.args.get("expressions", [])
            )
            for h in existing_hints
        )
        if not has_nolock:
            nolock_hint = exp.WithTableHint(expressions=[exp.Var(this="NOLOCK")])
            tbl.set("hints", existing_hints + [nolock_hint])
    return [("NOLOCK", ast_c)]


def _transform_recompile(ast):
    existing_options = ast.args.get("options") or []
    for opt in existing_options:
        if hasattr(opt, "this") and str(getattr(opt, "this", "")).upper() == "RECOMPILE":
            return []
    ast_c = ast.copy()
    current = list(ast_c.args.get("options") or [])
    current.append(exp.QueryOption(this=exp.Var(this="RECOMPILE")))
    ast_c.set("options", current)
    return [("RECOMPILE", ast_c)]


def _build_correlated_exists(inner_select, inner_col, outer_col):
    inner_select.set("expressions", [exp.Literal.number(1)])
    corr_cond = exp.EQ(this=inner_col, expression=outer_col.copy())
    existing_where = inner_select.args.get("where")
    if existing_where:
        new_where = exp.Where(this=exp.And(this=existing_where.this.copy(), expression=corr_cond))
    else:
        new_where = exp.Where(this=corr_cond)
    inner_select.set("where", new_where)
    return exp.Exists(this=inner_select)


def _transform_in_to_exists(ast):
    in_exprs = [i for i in ast.find_all(exp.In) if i.args.get("query") is not None]
    if not in_exprs:
        return []
    results = []
    for idx, in_node in enumerate(in_exprs):
        outer_col = in_node.args.get("this")
        subq = in_node.args.get("query")
        if outer_col is None or subq is None:
            continue
        inner_select = subq.this.copy() if isinstance(subq, exp.Subquery) else subq.copy()
        inner_exprs = inner_select.args.get("expressions", [])
        if not inner_exprs:
            continue
        inner_col = inner_exprs[0].copy()

        exists_expr = _build_correlated_exists(inner_select, inner_col, outer_col)
        ast_c = ast.copy()
        in_nodes_c = [i for i in ast_c.find_all(exp.In) if i.args.get("query") is not None]
        if idx < len(in_nodes_c):
            in_nodes_c[idx].replace(exists_expr)
            label = "IN\u2192EXISTS" if len(in_exprs) == 1 else f"IN\u2192EXISTS[{idx+1}]"
            results.append((label, ast_c))
    return results


def _transform_or_to_union(ast):
    where_node = ast.args.get("where")
    if where_node is None:
        return []
    or_cond = where_node.find(exp.Or)
    if or_cond is None:
        return []

    ast_c1 = ast.copy()
    or_in_c1 = ast_c1.args["where"].find(exp.Or)
    or_in_c1.replace(or_in_c1.left.copy())

    ast_c2 = ast.copy()
    or_in_c2 = ast_c2.args["where"].find(exp.Or)
    or_in_c2.replace(or_in_c2.right.copy())

    union = exp.union(ast_c1, ast_c2, distinct=False)
    return [("OR\u2192UNION ALL", union)]


def _transform_distinct_to_groupby(ast):
    if ast.args.get("distinct") is None:
        return []
    select_exprs = ast.args.get("expressions", [])
    if not select_exprs:
        return []

    ast_c = ast.copy()
    ast_c.set("distinct", None)
    group_cols = [e.copy() for e in ast_c.args.get("expressions", [])]
    ast_c.set("group", exp.Group(expressions=group_cols))
    return [("DISTINCT\u2192GROUP BY", ast_c)]


def _transform_subquery_to_cte(ast):
    if not isinstance(ast, exp.Select):
        return []
    from_node = ast.args.get("from_")
    if from_node is None:
        return []
    sq_node = from_node.find(exp.Subquery)
    if sq_node is None:
        return []
    alias = sq_node.alias or "cte_1"
    inner_select = sq_node.this.copy()

    ast_c = ast.copy()
    from_in_c = ast_c.args.get("from_")
    sq_in_c = from_in_c.find(exp.Subquery)
    if sq_in_c is None:
        return []

    table_ref = exp.Table(this=exp.to_identifier(alias))
    sq_in_c.replace(table_ref)

    cte_node = exp.CTE(
        this=inner_select,
        alias=exp.TableAlias(this=exp.to_identifier(alias)),
    )
    ast_c.set("with_", exp.With(expressions=[cte_node]))
    return [("Subquery\u2192CTE", ast_c)]


def _transform_join_reorder(ast):
    if not isinstance(ast, exp.Select):
        return []
    top_joins = ast.args.get("joins") or []
    inner_joins = [j for j in top_joins if _is_inner_join(j)]
    if len(inner_joins) < 2:
        return []
    ast_c = ast.copy()
    top_joins_c = ast_c.args.get("joins") or []
    inner_joins_c = [j for j in top_joins_c if _is_inner_join(j)]

    j0, j1 = inner_joins_c[0], inner_joins_c[-1]
    t0 = j0.args.get("this").copy() if j0.args.get("this") else None
    o0 = j0.args.get("on").copy() if j0.args.get("on") else None
    t1 = j1.args.get("this").copy() if j1.args.get("this") else None
    o1 = j1.args.get("on").copy() if j1.args.get("on") else None

    if t0 and t1:
        j0.set("this", t1)
        j0.set("on", o1)
        j1.set("this", t0)
        j1.set("on", o0)
        return [("JOIN reorder", ast_c)]
    return []


def _transform_cross_apply(ast):
    if not isinstance(ast, exp.Select):
        return []
    top_joins = ast.args.get("joins") or []
    candidates = [j for j in top_joins if j.find(exp.Subquery) and _is_inner_join(j)]
    if not candidates:
        return []
    results = []
    for idx, join_node in enumerate(candidates):
        sq = join_node.find(exp.Subquery)
        if sq is None:
            continue

        ast_c = ast.copy()
        top_joins_c = ast_c.args.get("joins") or []
        cands_c = [j for j in top_joins_c if j.find(exp.Subquery) and _is_inner_join(j)]
        if idx >= len(cands_c):
            continue
        target_join = cands_c[idx]
        sq_c = target_join.find(exp.Subquery)
        sq_alias = sq_c.alias or "apply_result"

        lateral = exp.Lateral(
            this=sq_c.copy(),
            alias=exp.TableAlias(this=exp.to_identifier(sq_alias)),
            cross_apply=True,
        )
        target_join.set("this", lateral)
        target_join.set("on", None)
        target_join.set("kind", None)

        label = "CROSS APPLY" if len(candidates) == 1 else f"CROSS APPLY[{idx+1}]"
        results.append((label, ast_c))
    return results


def _transform_join_hints(ast):
    joins = [j for j in ast.find_all(exp.Join) if _is_inner_join(j)]
    if not joins:
        return []
    results = []
    for hint_name in ("HASH", "MERGE", "LOOP"):
        ast_c = ast.copy()
        existing_options = list(ast_c.args.get("options") or [])
        existing_options.append(exp.QueryOption(this=exp.Var(this=f"{hint_name} JOIN")))
        ast_c.set("options", existing_options)
        results.append((f"{hint_name} JOIN", ast_c))
    return results


def _full_table_name(tbl):
    db = tbl.args.get("db")
    catalog = tbl.args.get("catalog")
    parts = []
    if catalog:
        parts.append(f"[{catalog.name}]")
    if db:
        parts.append(f"[{db.name}]")
    parts.append(f"[{tbl.name}]")
    return ".".join(parts)


def _build_alias_map(ast):
    alias_map = {}
    from_node = ast.args.get("from_")
    if from_node:
        tbl = from_node.find(exp.Table)
        if tbl and not tbl.find(exp.Subquery):
            alias_map[tbl.alias or tbl.name] = tbl
    for join_node in ast.find_all(exp.Join):
        tbl = join_node.find(exp.Table)
        if tbl and not tbl.find(exp.Subquery):
            alias_map[tbl.alias or tbl.name] = tbl
    return alias_map


def _collect_cols_from(node, alias_map, candidates):
    if node is None:
        return
    for col in node.find_all(exp.Column):
        tbl_alias = col.table
        col_name = col.name
        if not col_name:
            continue
        if tbl_alias and tbl_alias in alias_map:
            candidates.append((_full_table_name(alias_map[tbl_alias]), col_name))
        elif not tbl_alias and len(alias_map) == 1:
            candidates.append((_full_table_name(next(iter(alias_map.values()))), col_name))


def _collect_index_candidates(ast):
    alias_map = _build_alias_map(ast)
    candidates = []
    _collect_cols_from(ast.args.get("where"), alias_map, candidates)
    for join_node in ast.find_all(exp.Join):
        _collect_cols_from(join_node.args.get("on"), alias_map, candidates)
    seen = set()
    unique = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _transform_index_suggestions(ast):
    candidates = _collect_index_candidates(ast)
    if not candidates:
        return []

    comment_lines = [f"-- Consider index on {tbl}([{col}])" for tbl, col in candidates]
    comment_block = "\n".join(comment_lines)
    new_sql = comment_block + "\n" + _to_sql(ast)
    return [("Index suggestions", new_sql)]


_TRANSFORMS = [
    _transform_join_to_exists,
    _transform_top_n,
    _transform_nolock,
    _transform_recompile,
    _transform_in_to_exists,
    _transform_or_to_union,
    _transform_distinct_to_groupby,
    _transform_subquery_to_cte,
    _transform_join_reorder,
    _transform_cross_apply,
    _transform_join_hints,
    _transform_index_suggestions,
]


def _apply_transforms(ast, transform_fns):
    variants = []
    for transform_fn in transform_fns:
        try:
            results = transform_fn(ast)
            for label, result_ast in results:
                sql = result_ast if isinstance(result_ast, str) else result_ast.sql(dialect="tsql")
                variants.append((label, sql))
        except Exception as exc:
            print(f"\u26a0\ufe0f  Transform {transform_fn.__name__} skipped: {exc}", file=sys.stderr)
    return variants


def generate_variants(base_query):
    max_variants = 60
    try:
        max_variants = int(os.getenv("MAX_VARIANTS", "60"))
    except ValueError:
        print("⚠️  Warning: MAX_VARIANTS must be an integer. Using default of 60.", file=sys.stderr)
    try:
        ast = sqlglot.parse_one(base_query, dialect="tsql")
    except sqlglot.errors.ParseError as e:
        errors = e.errors if e.errors else [{}]
        first = errors[0] if errors else {}
        raise VariantGenerationError(
            f"Failed to parse SQL query: {e}",
            line=first.get("line"),
            col=first.get("col"),
            fragment=first.get("highlight") or first.get("fragment"),
            suggestion="Check that the query uses valid T-SQL syntax.",
        ) from e

    variants = _apply_transforms(ast, _TRANSFORMS)

    if len(variants) > max_variants:
        print(
            f"⚠️  Warning: {len(variants)} variants generated, limiting to MAX_VARIANTS={max_variants}."
        )
        variants = variants[:max_variants]

    return variants