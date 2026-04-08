# aggregator.py
import statistics


def compute_stats(values):
    if not values:
        return None
    if len(values) == 1:
        v = values[0]
        return {"mean": v, "median": v, "stdev": 0.0, "min": v, "max": v}
    return {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "stdev": statistics.stdev(values),
        "min": min(values),
        "max": max(values),
    }


def aggregate_runs(run_results):
    valid = [r for r in run_results if not r.get("error")]
    if not valid:
        return {}

    times = [r["time"] for r in valid if r.get("time") is not None]

    metric_keys = [
        "cpu_time_ms", "elapsed_time_ms",
        "logical_reads", "physical_reads", "read_ahead_reads",
        "lob_logical_reads", "lob_physical_reads",
    ]
    metrics_agg = {}
    for key in metric_keys:
        values = [r["server_metrics"][key] for r in valid if r.get("server_metrics", {}).get(key) is not None]
        stats = compute_stats(values)
        if stats is not None:
            metrics_agg[key] = stats

    first = valid[0]
    return {
        "time": compute_stats(times),
        "server_metrics": metrics_agg,
        "execution_plan": first.get("execution_plan", {}),
        "plan_xml": first.get("plan_xml"),
        "query_store": first.get("query_store"),
        "warnings": first.get("warnings", []),
    }
