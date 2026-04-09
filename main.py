# main.py
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from aggregator import aggregate_runs
from db import get_connection
from guardrails import check_variant
from runner import run_query
from validator import get_row_count, validate_row_count
from variants import generate_variants, VariantGenerationError

logger = logging.getLogger(__name__)
result_logger = logging.getLogger("benchmark")


def load_query():
    with open("query.sql", "r") as f:
        return f.read()


def parse_args():
    parser = argparse.ArgumentParser(description="AutoResearch SQL Server benchmarking tool")
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        metavar="N",
        help="Number of benchmark runs per variant (default: 1, max: 100)",
    )
    args = parser.parse_args()
    args.runs = max(1, min(args.runs, 100))
    return args


def setup_logging():
    load_dotenv(override=False)
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    numeric_level = getattr(logging, log_level, None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(numeric_level)
    stderr_handler.setFormatter(fmt)
    root.addHandler(stderr_handler)
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    log_filename = logs_dir / f"autoresearch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = logging.FileHandler(log_filename, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    bench = logging.getLogger("benchmark")
    bench.setLevel(logging.DEBUG)
    bench.addHandler(file_handler)
    bench.propagate = False


def save_results(results):
    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)


def _save_plan(plan_xml, variant_index):
    plans_dir = Path("plans")
    plans_dir.mkdir(exist_ok=True)
    plan_path = plans_dir / f"plan_variant_{variant_index}.sqlplan"
    plan_path.write_text(plan_xml, encoding="utf-8")
    return str(plan_path)


def _print_variant_result(result, index, total, label, num_runs=1):
    if num_runs > 1:
        print(f"Test {index}/{total} [{label}] ({num_runs} runs)")
        result_logger.info("Test %d/%d [%s] (%d runs)", index, total, label, num_runs)
        time_stats = result.get("time", {})
        if time_stats:
            print(f"⏱️  Time: {time_stats['mean']:.4f}s mean ± {time_stats['stdev']:.4f}s (median: {time_stats['median']:.4f}s)")
            result_logger.info("Time: %.4fs mean +/- %.4fs (median: %.4fs)", time_stats["mean"], time_stats["stdev"], time_stats["median"])
        sm = result.get("server_metrics", {})
        ep = result.get("execution_plan", {})
        cpu = sm.get("cpu_time_ms", {})
        if cpu:
            print(f"⚡ CPU: {cpu['mean']:.0f}ms mean ± {cpu['stdev']:.0f}ms (median: {cpu['median']:.0f}ms)")
            result_logger.info("CPU: %.0fms mean +/- %.0fms (median: %.0fms)", cpu["mean"], cpu["stdev"], cpu["median"])
        logical = sm.get("logical_reads", {})
        physical = sm.get("physical_reads", {})
        if logical:
            phys_str = f", {physical['median']:.0f} physical reads (median)" if physical else ""
            print(f"📊 IO: {logical['median']:.0f} logical reads (median){phys_str}")
            result_logger.info("IO: %.0f logical reads (median)%s", logical["median"], phys_str)
        memory_grant = ep.get("memory_grant_kb")
        if memory_grant is not None:
            print(f"💾 Memory grant: {memory_grant} KB")
            result_logger.info("Memory grant: %s KB", memory_grant)
        if ep.get("spill_warnings"):
            print("⚠️  SpillToTempDb detected!")
            logger.warning("SpillToTempDb detected in [%s]", label)
        for warning in result.get("warnings", []):
            print(f"⚠️  {warning}")
            logger.warning("[%s]: %s", label, warning)
    else:
        print(f"Test {index}/{total} [{label}]")
        result_logger.info("Test %d/%d [%s]", index, total, label)
        sm = result.get("server_metrics", {})
        ep = result.get("execution_plan", {})
        cpu = sm.get("cpu_time_ms")
        elapsed = sm.get("elapsed_time_ms")
        if cpu is not None and elapsed is not None:
            print(f"⏱️  Time: {result['time']:.4f}s (server: {cpu}ms CPU / {elapsed}ms elapsed)")
            result_logger.info("Time: %.4fs (server: %sms CPU / %sms elapsed)", result["time"], cpu, elapsed)
        else:
            print(f"⏱️  Time: {result['time']:.4f}s")
            result_logger.info("Time: %.4fs", result["time"])
        logical = sm.get("logical_reads")
        physical = sm.get("physical_reads")
        if logical is not None:
            print(f"📊 IO: {logical} logical reads, {physical} physical reads")
            result_logger.info("IO: %s logical reads, %s physical reads", logical, physical)
        memory_grant = ep.get("memory_grant_kb")
        if memory_grant is not None:
            print(f"💾 Memory grant: {memory_grant} KB")
            result_logger.info("Memory grant: %s KB", memory_grant)
        if ep.get("spill_warnings"):
            print("⚠️  SpillToTempDb detected!")
            logger.warning("SpillToTempDb detected in [%s]", label)
        for warning in result.get("warnings", []):
            print(f"⚠️  {warning}")
            logger.warning("[%s]: %s", label, warning)


def _print_ranking(results, num_runs=1):
    if not results:
        return
    print("\n🏆 RANKING:")
    result_logger.info("RANKING:")
    if num_runs > 1:
        valid = [r for r in results if isinstance(r.get("time"), dict) and r["time"].get("median") is not None]
        if valid:
            best_time = min(valid, key=lambda r: r["time"]["median"])
            print(f"  ⏱️  Best by time (median):  [{best_time['label']}] — {best_time['time']['median']:.4f}s")
            result_logger.info("  Best by time (median): [%s] — %.4fs", best_time["label"], best_time["time"]["median"])
        logical_results = [
            r for r in results if isinstance(r.get("server_metrics", {}).get("logical_reads"), dict)
        ]
        if logical_results:
            best_io = min(logical_results, key=lambda r: r["server_metrics"]["logical_reads"]["median"])
            print(f"  📊 Best by IO (median):     [{best_io['label']}] — {best_io['server_metrics']['logical_reads']['median']:.0f} logical reads")
            result_logger.info("  Best by IO (median): [%s] — %.0f logical reads", best_io["label"], best_io["server_metrics"]["logical_reads"]["median"])
        cpu_results = [
            r for r in results if isinstance(r.get("server_metrics", {}).get("cpu_time_ms"), dict)
        ]
        if cpu_results:
            best_cpu = min(cpu_results, key=lambda r: r["server_metrics"]["cpu_time_ms"]["median"])
            print(f"  ⚡ Best by CPU (median):    [{best_cpu['label']}] — {best_cpu['server_metrics']['cpu_time_ms']['median']:.0f}ms")
            result_logger.info("  Best by CPU (median): [%s] — %.0fms", best_cpu["label"], best_cpu["server_metrics"]["cpu_time_ms"]["median"])
    else:
        valid = [r for r in results if r.get("time") is not None]
        if valid:
            best_time = min(valid, key=lambda r: r["time"])
            print(f"  ⏱️  Best by time:         [{best_time['label']}] — {best_time['time']:.4f}s")
            result_logger.info("  Best by time: [%s] — %.4fs", best_time["label"], best_time["time"])
        logical_results = [
            r for r in results if r.get("server_metrics", {}).get("logical_reads") is not None
        ]
        if logical_results:
            best_io = min(logical_results, key=lambda r: r["server_metrics"]["logical_reads"])
            print(f"  📊 Best by IO:            [{best_io['label']}] — {best_io['server_metrics']['logical_reads']} logical reads")
            result_logger.info("  Best by IO: [%s] — %s logical reads", best_io["label"], best_io["server_metrics"]["logical_reads"])
        cpu_results = [
            r for r in results if r.get("server_metrics", {}).get("cpu_time_ms") is not None
        ]
        if cpu_results:
            best_cpu = min(cpu_results, key=lambda r: r["server_metrics"]["cpu_time_ms"])
            print(f"  ⚡ Best by CPU:            [{best_cpu['label']}] — {best_cpu['server_metrics']['cpu_time_ms']}ms")
            result_logger.info("  Best by CPU: [%s] — %sms", best_cpu["label"], best_cpu["server_metrics"]["cpu_time_ms"])
    mem_results = [
        r for r in results if r.get("execution_plan", {}).get("memory_grant_kb") is not None
    ]
    if mem_results:
        best_mem = min(mem_results, key=lambda r: r["execution_plan"]["memory_grant_kb"])
        print(f"  💾 Best by memory grant:  [{best_mem['label']}] — {best_mem['execution_plan']['memory_grant_kb']} KB")
        result_logger.info("  Best by memory grant: [%s] — %s KB", best_mem["label"], best_mem["execution_plan"]["memory_grant_kb"])
    spill_variants = [
        r["label"] for r in results if r.get("execution_plan", {}).get("spill_warnings")
    ]
    if spill_variants:
        spill_str = ", ".join(f"[{v}]" for v in spill_variants)
        print(f"  ⚠️  SpillToTempDb:         {spill_str}")
        logger.warning("SpillToTempDb in ranking: %s", spill_str)


def _compute_base_count(base_query):
    try:
        conn = get_connection()
        try:
            count = get_row_count(base_query, conn)
            logger.info("Base row count: %d", count)
            return count, conn
        except Exception as exc:
            conn.close()
            logger.warning("Could not compute base row count — runtime validation disabled: %s", exc)
            return None, None
    except Exception as exc:
        logger.warning("Could not open connection for row count — runtime validation disabled: %s", exc)
        return None, None


def _check_guardrails(base_query, query, label):
    violations = check_variant(base_query, query, label)
    block_violations = [v for v in violations if v.severity == "block"]
    warn_violations = [v for v in violations if v.severity == "warn"]

    if block_violations:
        for v in block_violations:
            logger.warning("Guardrail [%s] blocked [%s]: %s", v.rule_id, label, v.message)
        return [], True

    warnings = [v.message for v in warn_violations]
    for msg in warnings:
        logger.warning("Guardrail [%s]: %s", label, msg)
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
        logger.warning("Row count mismatch blocked [%s]: %s", label, val_result.message)
        return validation_info, True
    if val_result.variant_count == -1:
        logger.warning("[%s]: %s", label, val_result.message)
    return validation_info, False


def main():
    setup_logging()
    args = parse_args()
    num_runs = args.runs
    base_query = load_query()

    try:
        variants = generate_variants(base_query)
    except VariantGenerationError as e:
        logger.error("Failed to generate variants: %s", e)
        sys.exit(1)

    if not variants:
        logger.info("No variants generated for this query.")
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

            if num_runs == 1:
                result = run_query(query)

                if result["error"]:
                    logger.error("Test %d/%d [%s] — Error: %s", i + 1, len(variants), label, result["error"])
                    continue

                _print_variant_result(result, i + 1, len(variants), label, num_runs=1)

                plan_file = None
                if result.get("plan_xml"):
                    plan_file = _save_plan(result["plan_xml"], i + 1)

                all_results.append({**result, "label": label})
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
            else:
                run_results = []
                for run_i in range(num_runs):
                    result = run_query(query, collect_plan=(run_i == 0))
                    if not result["error"]:
                        run_results.append(result)
                    else:
                        logger.warning("[%s] run %d/%d error: %s", label, run_i + 1, num_runs, result["error"])

                if not run_results:
                    logger.error("Test %d/%d [%s] — All %d runs failed", i + 1, len(variants), label, num_runs)
                    continue

                agg = aggregate_runs(run_results)
                _print_variant_result(agg, i + 1, len(variants), label, num_runs=len(run_results))

                plan_file = None
                if agg.get("plan_xml"):
                    plan_file = _save_plan(agg["plan_xml"], i + 1)

                all_results.append({**agg, "label": label})
                combined_warnings = agg.get("warnings", []) + guardrail_warnings
                raw_runs = [
                    {"time": r["time"], "server_metrics": r.get("server_metrics", {})}
                    for r in run_results
                ]
                json_results.append({
                    "label": label,
                    "query": query,
                    "runs": num_runs,
                    "time": agg["time"],
                    "server_metrics": agg.get("server_metrics", {}),
                    "execution_plan": agg.get("execution_plan", {}),
                    "execution_plan_file": plan_file,
                    "query_store": agg.get("query_store"),
                    "warnings": combined_warnings,
                    "validation": validation_info,
                    "guardrail_warnings": guardrail_warnings,
                    "raw_runs": raw_runs,
                })
    finally:
        if val_conn is not None:
            val_conn.close()

    _print_ranking(all_results, num_runs=num_runs)
    save_results(json_results)


if __name__ == "__main__":
    main()
