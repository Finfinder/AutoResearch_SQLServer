# main.py
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import colorlog
from dotenv import load_dotenv
from aggregator import aggregate_runs
from db import get_connection
from guardrails import check_variant
from runner import run_query
from validator import build_strict_validation_context, get_row_count, validate_query_results
from variants import generate_variants, VariantGenerationError

logger = logging.getLogger(__name__)
result_logger = logging.getLogger("benchmark")

_VARIANT_WARNING_FORMAT = "[%s]: %s"
AUTO_STRICT_VALIDATION_ROW_THRESHOLD = 200
_VALIDATION_CONNECTION_UNAVAILABLE = "Validation connection unavailable"


def get_resource_path(relative_path):
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return resource_root / Path(relative_path)


def get_runtime_output_path(relative_path):
    if getattr(sys, "frozen", False):
        runtime_root = Path(sys.executable).resolve().parent
    else:
        runtime_root = Path(__file__).resolve().parent
    return runtime_root / Path(relative_path)


def load_query():
    return get_resource_path("query.sql").read_text(encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description="AutoResearch SQL Server benchmarking tool")
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        metavar="N",
        help="Number of benchmark runs per variant (default: 1, max: 100)",
    )
    parser.add_argument(
        "--strict-validation",
        action="store_true",
        help=(
            "Force strict runtime validation based on full result hashing. "
            f"Without this flag, strict validation is applied automatically only below "
            f"{AUTO_STRICT_VALIDATION_ROW_THRESHOLD} base rows."
        ),
    )
    args = parser.parse_args()
    args.runs = max(1, min(args.runs, 100))
    return args


def setup_logging():
    env_path = get_runtime_output_path(".env")
    if env_path.exists():
        load_dotenv(env_path, override=False)
    else:
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
    use_color = sys.stderr.isatty() and "NO_COLOR" not in os.environ
    if use_color:
        color_fmt = colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s %(levelname)s%(reset)s %(name)s: %(message)s",
            log_colors={
                "DEBUG": "white",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
        stderr_handler.setFormatter(color_fmt)
    else:
        stderr_handler.setFormatter(fmt)
    root.addHandler(stderr_handler)
    logs_dir = get_runtime_output_path("logs")
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
    with get_runtime_output_path("results.json").open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)


def _save_plan(plan_xml, variant_index):
    plans_dir = get_runtime_output_path("plans")
    plans_dir.mkdir(exist_ok=True)
    plan_path = plans_dir / f"plan_variant_{variant_index}.sqlplan"
    plan_path.write_text(plan_xml, encoding="utf-8")
    return str(plan_path)


def _print_variant_header(index, total, label, num_runs):
    if num_runs > 1:
        print(f"Test {index}/{total} [{label}] ({num_runs} runs)")
        result_logger.info("Test %d/%d [%s] (%d runs)", index, total, label, num_runs)
        return

    print(f"Test {index}/{total} [{label}]")
    result_logger.info("Test %d/%d [%s]", index, total, label)


def _print_spill_warning(label, execution_plan):
    if not execution_plan.get("spill_warnings"):
        return

    print("⚠️  SpillToTempDb detected!")
    logger.warning("SpillToTempDb detected in [%s]", label)


def _print_variant_warnings(label, warnings):
    for warning in warnings:
        print(f"⚠️  {warning}")
        logger.warning(_VARIANT_WARNING_FORMAT, label, warning)


def _print_aggregated_variant_metrics(result):
    time_stats = result.get("time", {})
    if time_stats:
        print(
            f"⏱️  Time: {time_stats['mean']:.4f}s mean ± {time_stats['stdev']:.4f}s "
            f"(median: {time_stats['median']:.4f}s)"
        )
        result_logger.info(
            "Time: %.4fs mean +/- %.4fs (median: %.4fs)",
            time_stats["mean"],
            time_stats["stdev"],
            time_stats["median"],
        )

    server_metrics = result.get("server_metrics", {})
    cpu_stats = server_metrics.get("cpu_time_ms", {})
    if cpu_stats:
        print(
            f"⚡ CPU: {cpu_stats['mean']:.0f}ms mean ± {cpu_stats['stdev']:.0f}ms "
            f"(median: {cpu_stats['median']:.0f}ms)"
        )
        result_logger.info(
            "CPU: %.0fms mean +/- %.0fms (median: %.0fms)",
            cpu_stats["mean"],
            cpu_stats["stdev"],
            cpu_stats["median"],
        )

    logical_reads = server_metrics.get("logical_reads", {})
    physical_reads = server_metrics.get("physical_reads", {})
    if logical_reads:
        phys_str = f", {physical_reads['median']:.0f} physical reads (median)" if physical_reads else ""
        print(f"📊 IO: {logical_reads['median']:.0f} logical reads (median){phys_str}")
        result_logger.info("IO: %.0f logical reads (median)%s", logical_reads["median"], phys_str)


def _print_single_run_variant_metrics(result):
    server_metrics = result.get("server_metrics", {})
    cpu = server_metrics.get("cpu_time_ms")
    elapsed = server_metrics.get("elapsed_time_ms")
    if cpu is not None and elapsed is not None:
        print(f"⏱️  Time: {result['time']:.4f}s (server: {cpu}ms CPU / {elapsed}ms elapsed)")
        result_logger.info("Time: %.4fs (server: %sms CPU / %sms elapsed)", result["time"], cpu, elapsed)
    else:
        print(f"⏱️  Time: {result['time']:.4f}s")
        result_logger.info("Time: %.4fs", result["time"])

    logical_reads = server_metrics.get("logical_reads")
    physical_reads = server_metrics.get("physical_reads")
    if logical_reads is not None:
        print(f"📊 IO: {logical_reads} logical reads, {physical_reads} physical reads")
        result_logger.info("IO: %s logical reads, %s physical reads", logical_reads, physical_reads)


def _print_memory_grant(result):
    memory_grant = result.get("execution_plan", {}).get("memory_grant_kb")
    if memory_grant is None:
        return

    print(f"💾 Memory grant: {memory_grant} KB")
    result_logger.info("Memory grant: %s KB", memory_grant)


def _print_variant_result(result, index, total, label, num_runs=1):
    _print_variant_header(index, total, label, num_runs)
    if num_runs > 1:
        _print_aggregated_variant_metrics(result)
    else:
        _print_single_run_variant_metrics(result)

    _print_memory_grant(result)
    _print_spill_warning(label, result.get("execution_plan", {}))
    _print_variant_warnings(label, result.get("warnings", []))


def _get_ranking_value(result, metric_path, aggregate=False):
    value = result
    for key in metric_path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)

    if aggregate:
        if isinstance(value, dict):
            return value.get("median")
        return None

    if isinstance(value, dict):
        return None
    return value


def _get_best_ranked_result(results, metric_path, aggregate=False):
    ranked = []
    for result in results:
        value = _get_ranking_value(result, metric_path, aggregate=aggregate)
        if value is not None:
            ranked.append((result, value))

    if not ranked:
        return None, None

    return min(ranked, key=lambda item: item[1])


def _print_best_rank_entry(print_prefix, log_template, best_result, value, value_format):
    print(f"  {print_prefix}[{best_result['label']}] — {value_format.format(value)}")
    result_logger.info(log_template, best_result["label"], value)


def _print_primary_ranking(results, aggregate=False):
    ranking_specs = [
        {
            "path": ("time",),
            "print_prefix": "⏱️  Best by time (median):  " if aggregate else "⏱️  Best by time:         ",
            "log_template": "  Best by time (median): [%s] — %.4fs" if aggregate else "  Best by time: [%s] — %.4fs",
            "value_format": "{:.4f}s",
        },
        {
            "path": ("server_metrics", "logical_reads"),
            "print_prefix": "📊 Best by IO (median):     " if aggregate else "📊 Best by IO:            ",
            "log_template": "  Best by IO (median): [%s] — %.0f logical reads" if aggregate else "  Best by IO: [%s] — %s logical reads",
            "value_format": "{:.0f} logical reads" if aggregate else "{} logical reads",
        },
        {
            "path": ("server_metrics", "cpu_time_ms"),
            "print_prefix": "⚡ Best by CPU (median):    " if aggregate else "⚡ Best by CPU:            ",
            "log_template": "  Best by CPU (median): [%s] — %.0fms" if aggregate else "  Best by CPU: [%s] — %sms",
            "value_format": "{:.0f}ms" if aggregate else "{}ms",
        },
    ]

    for spec in ranking_specs:
        best_result, value = _get_best_ranked_result(results, spec["path"], aggregate=aggregate)
        if best_result is None:
            continue
        _print_best_rank_entry(
            spec["print_prefix"],
            spec["log_template"],
            best_result,
            value,
            spec["value_format"],
        )


def _print_ranking(results, num_runs=1):
    if not results:
        return

    print("\n🏆 RANKING:")
    result_logger.info("RANKING:")
    if num_runs > 1:
        _print_primary_ranking(results, aggregate=True)
    else:
        _print_primary_ranking(results)

    best_mem, memory_grant = _get_best_ranked_result(
        results,
        ("execution_plan", "memory_grant_kb"),
    )
    if best_mem is not None:
        print(f"  💾 Best by memory grant:  [{best_mem['label']}] — {memory_grant} KB")
        result_logger.info("  Best by memory grant: [%s] — %s KB", best_mem["label"], memory_grant)

    spill_variants = [
        result["label"] for result in results if result.get("execution_plan", {}).get("spill_warnings")
    ]
    if spill_variants:
        spill_str = ", ".join(f"[{variant}]" for variant in spill_variants)
        print(f"  ⚠️  SpillToTempDb:         {spill_str}")
        logger.warning("SpillToTempDb in ranking: %s", spill_str)


def _reset_bench_conn(bench_conn, label=""):
    try:
        if bench_conn is not None:
            bench_conn.close()
    except Exception:
        pass
    try:
        new_conn = get_connection()
        logger.info("Połączenie benchmarkowe odtworzone po błędzie runu [%s].", label)
        return new_conn
    except Exception as exc:
        logger.error("Nie udało się odtworzyć połączenia benchmarkowego: %s", exc)
        return None


def _compute_base_count(base_query, strict_requested=False):
    try:
        conn = get_connection()
        try:
            count = get_row_count(base_query, conn)
            logger.info("Base row count: %d", count)
            return count, conn
        except Exception as exc:
            if strict_requested:
                logger.warning(
                    "Could not compute base row count — strict validation will attempt full result hashing: %s",
                    exc,
                )
                return None, conn
            conn.close()
            logger.warning("Could not compute base row count — runtime validation disabled: %s", exc)
            return None, None
    except Exception as exc:
        logger.warning("Could not open connection for row count — runtime validation disabled: %s", exc)
        return None, None


def _resolve_strict_validation_source(base_count, strict_requested):
    if strict_requested:
        return "cli"
    if base_count is not None and base_count < AUTO_STRICT_VALIDATION_ROW_THRESHOLD:
        return "auto"
    return None


def _build_base_strict_context(base_query, base_count, val_conn, strict_requested):
    strict_source = _resolve_strict_validation_source(base_count, strict_requested)
    if strict_source is None:
        return base_count, None, None
    if val_conn is None:
        return (
            base_count,
            {
                "ordered": False,
                "base_signature": None,
                "base_row_count": base_count,
                "fallback_reason": _VALIDATION_CONNECTION_UNAVAILABLE,
                "warnings": [_VALIDATION_CONNECTION_UNAVAILABLE],
            },
            strict_source,
        )

    strict_context = build_strict_validation_context(base_query, val_conn)
    strict_base_count = strict_context.get("base_row_count")
    if base_count is None and strict_base_count is not None:
        base_count = strict_base_count
        logger.info("Base row count recovered from strict validation context: %d", base_count)

    if strict_source == "cli":
        logger.info("Strict validation forced by CLI.")
    else:
        logger.info(
            "Strict validation enabled automatically for base row count below %d.",
            AUTO_STRICT_VALIDATION_ROW_THRESHOLD,
        )

    return base_count, strict_context, strict_source


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


def _build_validation_connection_unavailable_info(base_count, strict_context, strict_source):
    fallback_reason = (strict_context or {}).get("fallback_reason") or _VALIDATION_CONNECTION_UNAVAILABLE
    warnings = list((strict_context or {}).get("warnings", [])) or [fallback_reason]
    return {
        "is_valid": True,
        "base_count": base_count,
        "variant_count": -1,
        "message": (
            f"Strict validation skipped — {fallback_reason}; base row count unavailable"
            if base_count is None
            else f"Strict validation skipped — {fallback_reason}; runtime validation unavailable"
        ),
        "mode": "row_count",
        "ordered": (strict_context or {}).get("ordered", False),
        "strict_requested": True,
        "strict_applied": False,
        "strict_source": strict_source,
        "fallback_reason": fallback_reason,
        "warnings": warnings,
    }


def _serialize_validation_result(val_result):
    if hasattr(val_result, "to_dict"):
        return val_result.to_dict()
    return {
        "is_valid": val_result.is_valid,
        "base_count": val_result.base_count,
        "variant_count": val_result.variant_count,
        "message": val_result.message,
    }


def _check_row_count(base_count, query, label, val_conn, strict_context=None, strict_source=None):
    if val_conn is None:
        if strict_source is None:
            return None, False
        validation_info = _build_validation_connection_unavailable_info(base_count, strict_context, strict_source)
        logger.warning(_VARIANT_WARNING_FORMAT, label, validation_info["message"])
        return validation_info, False
    if base_count is None and strict_source is None:
        return None, False

    val_result = validate_query_results(
        base_count,
        query,
        val_conn,
        strict_requested=strict_source is not None,
        strict_source=strict_source,
        strict_context=strict_context,
    )
    validation_info = _serialize_validation_result(val_result)
    if not val_result.is_valid:
        logger.warning("Validation mismatch blocked [%s]: %s", label, val_result.message)
        return validation_info, True
    if val_result.fallback_reason or val_result.variant_count == -1:
        logger.warning(_VARIANT_WARNING_FORMAT, label, val_result.message)
    return validation_info, False


def _prepare_variant_execution(base_query, query, label, base_count, val_conn, strict_context=None, strict_source=None):
    guardrail_warnings, blocked = _check_guardrails(base_query, query, label)
    if blocked:
        return guardrail_warnings, None, True

    validation_info, blocked = _check_row_count(
        base_count,
        query,
        label,
        val_conn,
        strict_context,
        strict_source,
    )
    return guardrail_warnings, validation_info, blocked


def _save_plan_if_present(result, variant_index):
    if not result.get("plan_xml"):
        return None

    return _save_plan(result["plan_xml"], variant_index)


def _build_single_json_result(label, query, result, plan_file, validation_info, guardrail_warnings):
    combined_warnings = result.get("warnings", []) + guardrail_warnings
    return {
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
    }


def _build_raw_runs(run_results):
    return [
        {"time": run_result["time"], "server_metrics": run_result.get("server_metrics", {})}
        for run_result in run_results
    ]


def _build_multi_json_result(
    label,
    query,
    aggregated_result,
    plan_file,
    validation_info,
    guardrail_warnings,
    run_results,
    num_runs,
):
    combined_warnings = aggregated_result.get("warnings", []) + guardrail_warnings
    return {
        "label": label,
        "query": query,
        "runs": num_runs,
        "time": aggregated_result["time"],
        "server_metrics": aggregated_result.get("server_metrics", {}),
        "execution_plan": aggregated_result.get("execution_plan", {}),
        "execution_plan_file": plan_file,
        "query_store": aggregated_result.get("query_store"),
        "warnings": combined_warnings,
        "validation": validation_info,
        "guardrail_warnings": guardrail_warnings,
        "raw_runs": _build_raw_runs(run_results),
    }


def _run_single_variant(query, bench_conn, variant_index, total_variants, label, validation_info, guardrail_warnings):
    result = run_query(query, conn=bench_conn)
    if result["error"]:
        logger.error(
            "Test %d/%d [%s] — Error: %s",
            variant_index,
            total_variants,
            label,
            result["error"],
        )
        return None, None, _reset_bench_conn(bench_conn, label)

    _print_variant_result(result, variant_index, total_variants, label, num_runs=1)
    plan_file = _save_plan_if_present(result, variant_index)
    all_result = {**result, "label": label}
    json_result = _build_single_json_result(
        label,
        query,
        result,
        plan_file,
        validation_info,
        guardrail_warnings,
    )
    return all_result, json_result, bench_conn


def _run_multi_variant(
    query,
    bench_conn,
    variant_index,
    total_variants,
    label,
    num_runs,
    validation_info,
    guardrail_warnings,
):
    run_results = []
    for run_index in range(num_runs):
        result = run_query(query, collect_plan=(run_index == 0), conn=bench_conn)
        if not result["error"]:
            run_results.append(result)
            continue

        logger.warning("[%s] run %d/%d error: %s", label, run_index + 1, num_runs, result["error"])
        bench_conn = _reset_bench_conn(bench_conn, label)

    if not run_results:
        logger.error("Test %d/%d [%s] — All %d runs failed", variant_index, total_variants, label, num_runs)
        return None, None, bench_conn

    aggregated_result = aggregate_runs(run_results)
    _print_variant_result(
        aggregated_result,
        variant_index,
        total_variants,
        label,
        num_runs=len(run_results),
    )
    plan_file = _save_plan_if_present(aggregated_result, variant_index)
    all_result = {**aggregated_result, "label": label}
    json_result = _build_multi_json_result(
        label,
        query,
        aggregated_result,
        plan_file,
        validation_info,
        guardrail_warnings,
        run_results,
        num_runs,
    )
    return all_result, json_result, bench_conn


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

    base_count, val_conn = _compute_base_count(
        base_query,
        strict_requested=getattr(args, "strict_validation", False),
    )
    base_count, strict_context, strict_source = _build_base_strict_context(
        base_query,
        base_count,
        val_conn,
        getattr(args, "strict_validation", False),
    )

    bench_conn = None
    try:
        bench_conn = get_connection()
    except Exception as exc:
        logger.error("Nie udało się otworzyć połączenia benchmarkowego: %s", exc)
        sys.exit(1)

    all_results = []
    json_results = []

    try:
        total_variants = len(variants)
        for variant_index, (label, query) in enumerate(variants, start=1):
            guardrail_warnings, validation_info, blocked = _prepare_variant_execution(
                base_query,
                query,
                label,
                base_count,
                val_conn,
                strict_context,
                strict_source,
            )
            if blocked:
                continue

            if num_runs == 1:
                all_result, json_result, bench_conn = _run_single_variant(
                    query,
                    bench_conn,
                    variant_index,
                    total_variants,
                    label,
                    validation_info,
                    guardrail_warnings,
                )
            else:
                all_result, json_result, bench_conn = _run_multi_variant(
                    query,
                    bench_conn,
                    variant_index,
                    total_variants,
                    label,
                    num_runs,
                    validation_info,
                    guardrail_warnings,
                )

            if all_result is None:
                continue

            all_results.append(all_result)
            json_results.append(json_result)
    finally:
        if val_conn is not None:
            val_conn.close()
        if bench_conn is not None:
            bench_conn.close()

    _print_ranking(all_results, num_runs=num_runs)
    save_results(json_results)


if __name__ == "__main__":
    main()
