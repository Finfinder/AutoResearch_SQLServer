# main.py
import json
import sys
from pathlib import Path
from runner import run_query
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

    all_results = []
    json_results = []

    for i, (label, query) in enumerate(variants):
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
        json_results.append({
            "label": label,
            "query": query,
            "time": result["time"],
            "server_metrics": result.get("server_metrics", {}),
            "execution_plan": result.get("execution_plan", {}),
            "execution_plan_file": plan_file,
            "query_store": result.get("query_store"),
            "warnings": result.get("warnings", []),
        })

    _print_ranking(all_results)
    save_results(json_results)


if __name__ == "__main__":
    main()
