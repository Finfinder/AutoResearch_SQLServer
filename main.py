# main.py
import json
import sys
from pathlib import Path
from db import get_connection
from guardrails import check_variant
from runner import run_query
from validator import get_row_count, validate_row_count
from variants import generate_variants, VariantGenerationError


def load_query():
    with open("query.sql", "r") as f:
        return f.read()


def save_results(results):
    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)


def _save_plan(plan_xml, variant_index):
    plans_dir = Path("plans")
    plans_dir.mkdir(exist_ok=True)
    plan_path = plans_dir / f"plan_variant_{variant_index}.sqlplan"
    plan_path.write_text(plan_xml, encoding="utf-8")
    return str(plan_path)


def _print_variant_result(result, index, total, label):
    print(f"Test {index}/{total} [{label}]")
    sm = result.get("server_metrics", {})
    ep = result.get("execution_plan", {})
    cpu = sm.get("cpu_time_ms")
    elapsed = sm.get("elapsed_time_ms")
    if cpu is not None and elapsed is not None:
        print(f"⏱️  Time: {result['time']:.4f}s (server: {cpu}ms CPU / {elapsed}ms elapsed)")
    else:
        print(f"⏱️  Time: {result['time']:.4f}s")
    logical = sm.get("logical_reads")
    physical = sm.get("physical_reads")
    if logical is not None:
        print(f"📊 IO: {logical} logical reads, {physical} physical reads")
    memory_grant = ep.get("memory_grant_kb")
    if memory_grant is not None:
        print(f"💾 Memory grant: {memory_grant} KB")
    if ep.get("spill_warnings"):
        print("⚠️  SpillToTempDb detected!")
    for warning in result.get("warnings", []):
        print(f"⚠️  {warning}")


def _print_ranking(results):
    if not results:
        return
    print("\n🏆 RANKING:")
    valid = [r for r in results if r.get("time") is not None]
    if valid:
        best_time = min(valid, key=lambda r: r["time"])
        print(f"  ⏱️  Best by time:         [{best_time['label']}] — {best_time['time']:.4f}s")
    logical_results = [
        r for r in results if r.get("server_metrics", {}).get("logical_reads") is not None
    ]
    if logical_results:
        best_io = min(logical_results, key=lambda r: r["server_metrics"]["logical_reads"])
        print(f"  📊 Best by IO:            [{best_io['label']}] — {best_io['server_metrics']['logical_reads']} logical reads")
    cpu_results = [
        r for r in results if r.get("server_metrics", {}).get("cpu_time_ms") is not None
    ]
    if cpu_results:
        best_cpu = min(cpu_results, key=lambda r: r["server_metrics"]["cpu_time_ms"])
        print(f"  ⚡ Best by CPU:            [{best_cpu['label']}] — {best_cpu['server_metrics']['cpu_time_ms']}ms")
    mem_results = [
        r for r in results if r.get("execution_plan", {}).get("memory_grant_kb") is not None
    ]
    if mem_results:
        best_mem = min(mem_results, key=lambda r: r["execution_plan"]["memory_grant_kb"])
        print(f"  💾 Best by memory grant:  [{best_mem['label']}] — {best_mem['execution_plan']['memory_grant_kb']} KB")
    spill_variants = [
        r["label"] for r in results if r.get("execution_plan", {}).get("spill_warnings")
    ]
    if spill_variants:
        spill_str = ", ".join(f"[{v}]" for v in spill_variants)
        print(f"  ⚠️  SpillToTempDb:         {spill_str}")


def _compute_base_count(base_query):
    try:
        conn = get_connection()
        try:
            count = get_row_count(base_query, conn)
            print(f"ℹ️  Base row count: {count}")
            return count, conn
        except Exception as exc:
            conn.close()
            print(f"⚠️  Could not compute base row count — runtime validation disabled: {exc}", file=sys.stderr)
            return None, None
    except Exception as exc:
        print(f"⚠️  Could not open connection for row count — runtime validation disabled: {exc}", file=sys.stderr)
        return None, None


def _check_guardrails(base_query, query, label):
    violations = check_variant(base_query, query, label)
    block_violations = [v for v in violations if v.severity == "block"]
    warn_violations = [v for v in violations if v.severity == "warn"]

    if block_violations:
        for v in block_violations:
            print(f"🚫 Guardrail [{v.rule_id}] blocked [{label}]: {v.message}")
        return [], True

    warnings = [v.message for v in warn_violations]
    for msg in warnings:
        print(f"⚠️  Guardrail [{label}]: {msg}")
    return warnings, False


def _check_row_count(base_count, query, label, val_conn):
    if base_count is None or val_conn is None:
        return None, False

    val_result = validate_row_count(base_count, query, val_conn)
    validation_info = {
        "is_valid": val_result.is_valid,
        "base_count": val_result.base_count,
        "variant_count": val_result.variant_count,
        "message": val_result.message,
    }
    if not val_result.is_valid:
        print(f"🚫 Row count mismatch blocked [{label}]: {val_result.message}")
        return validation_info, True
    if val_result.variant_count == -1:
        print(f"⚠️  [{label}]: {val_result.message}")
    return validation_info, False


def main():
    base_query = load_query()

    try:
        variants = generate_variants(base_query)
    except VariantGenerationError as e:
        print(f"❌ Failed to generate variants: {e}")
        sys.exit(1)

    if not variants:
        print("ℹ️  No variants generated for this query.")
        return

    base_count, val_conn = _compute_base_count(base_query)

    all_results = []
    json_results = []

    try:
        for i, (label, query) in enumerate(variants):
            guardrail_warnings, blocked = _check_guardrails(base_query, query, label)
            if blocked:
                continue

            validation_info, blocked = _check_row_count(base_count, query, label, val_conn)
            if blocked:
                continue

            result = run_query(query)

            if result["error"]:
                print(f"Test {i+1}/{len(variants)} [{label}]")
                print(f"❌ Error: {result['error']}")
                continue

            _print_variant_result(result, i + 1, len(variants), label)

            plan_file = None
            if result.get("plan_xml"):
                plan_file = _save_plan(result["plan_xml"], i + 1)

            all_results.append({**result, "label": label})
            # "warnings" merges runner warnings + guardrail warnings for backward-compatible consumers.
            # "guardrail_warnings" retains the guardrail subset separately for finer-grained inspection.
            combined_warnings = result.get("warnings", []) + guardrail_warnings
            json_results.append({
                "label": label,
                "query": query,
                "time": result["time"],
                "server_metrics": result.get("server_metrics", {}),
                "execution_plan": result.get("execution_plan", {}),
                "execution_plan_file": plan_file,
                "query_store": result.get("query_store"),
                "warnings": combined_warnings,
                "validation": validation_info,
                "guardrail_warnings": guardrail_warnings,
            })
    finally:
        if val_conn is not None:
            val_conn.close()

    _print_ranking(all_results)
    save_results(json_results)


if __name__ == "__main__":
    main()
