# tests/test_aggregator.py
import pytest
from aggregator import aggregate_runs, compute_stats


class TestComputeStats:
    def test_multiple_values(self):
        result = compute_stats([1.0, 2.0, 3.0])
        assert result["mean"] == pytest.approx(2.0)
        assert result["median"] == pytest.approx(2.0)
        assert result["min"] == pytest.approx(1.0)
        assert result["max"] == pytest.approx(3.0)
        assert result["stdev"] > 0

    def test_single_value_has_zero_stdev(self):
        result = compute_stats([5.0])
        assert result["mean"] == pytest.approx(5.0)
        assert result["median"] == pytest.approx(5.0)
        assert result["stdev"] == pytest.approx(0.0)
        assert result["min"] == pytest.approx(5.0)
        assert result["max"] == pytest.approx(5.0)

    def test_empty_list_returns_none(self):
        assert compute_stats([]) is None

    def test_two_values_stdev(self):
        result = compute_stats([0.0, 4.0])
        assert result["mean"] == pytest.approx(2.0)
        assert result["stdev"] > 0


class TestAggregateRuns:
    def _make_run(self, time, cpu=None, logical=None, error=None):
        return {
            "time": time,
            "error": error,
            "server_metrics": {
                "cpu_time_ms": cpu,
                "elapsed_time_ms": None,
                "logical_reads": logical,
                "physical_reads": None,
                "read_ahead_reads": None,
            },
            "execution_plan": {"memory_grant_kb": 512},
            "plan_xml": "<plan/>",
            "query_store": {"avg_duration_us": 100},
            "warnings": ["w1"],
        }

    def test_aggregates_time_stats(self):
        runs = [self._make_run(0.1), self._make_run(0.3)]
        result = aggregate_runs(runs)
        assert result["time"]["mean"] == pytest.approx(0.2)
        assert result["time"]["median"] == pytest.approx(0.2)
        assert result["time"]["min"] == pytest.approx(0.1)
        assert result["time"]["max"] == pytest.approx(0.3)

    def test_aggregates_server_metrics(self):
        runs = [self._make_run(0.1, cpu=10, logical=100), self._make_run(0.2, cpu=20, logical=200)]
        result = aggregate_runs(runs)
        assert result["server_metrics"]["cpu_time_ms"]["mean"] == pytest.approx(15.0)
        assert result["server_metrics"]["logical_reads"]["mean"] == pytest.approx(150.0)

    def test_preserves_first_run_plan_and_query_store(self):
        runs = [self._make_run(0.1), self._make_run(0.2)]
        # Mutate second run's plan to verify first run's data is preserved
        runs[1]["execution_plan"] = {"memory_grant_kb": 999}
        runs[1]["plan_xml"] = "<other/>"
        runs[1]["query_store"] = {"avg_duration_us": 999}
        result = aggregate_runs(runs)
        assert result["execution_plan"] == {"memory_grant_kb": 512}
        assert result["plan_xml"] == "<plan/>"
        assert result["query_store"] == {"avg_duration_us": 100}

    def test_skips_error_runs(self):
        runs = [self._make_run(None, error="timeout"), self._make_run(0.2), self._make_run(0.4)]
        result = aggregate_runs(runs)
        assert result["time"]["mean"] == pytest.approx(0.3)
        assert result["time"]["min"] == pytest.approx(0.2)

    def test_all_error_runs_returns_empty(self):
        runs = [self._make_run(None, error="err1"), self._make_run(None, error="err2")]
        result = aggregate_runs(runs)
        assert result == {}

    def test_missing_server_metrics_key_excluded(self):
        runs = [
            {"time": 0.1, "error": None, "server_metrics": {}, "execution_plan": {}, "plan_xml": None, "query_store": None, "warnings": []},
            {"time": 0.2, "error": None, "server_metrics": {}, "execution_plan": {}, "plan_xml": None, "query_store": None, "warnings": []},
        ]
        result = aggregate_runs(runs)
        assert result["server_metrics"] == {}

    def test_first_run_error_fallback_to_second_run(self):
        # If run 0 (collect_plan=True) errors, valid[0] is run 1 (collect_plan=False).
        # Plan data from run 1 is empty — documents the expected behavior.
        runs = [
            {"time": None, "error": "timeout", "server_metrics": {}, "execution_plan": {"memory_grant_kb": 512}, "plan_xml": "<plan/>", "query_store": {"avg_duration_us": 100}, "warnings": []},
            {"time": 0.2, "error": None, "server_metrics": {}, "execution_plan": {}, "plan_xml": None, "query_store": None, "warnings": []},
        ]
        result = aggregate_runs(runs)
        assert result["time"]["mean"] == pytest.approx(0.2)
        # Plan data from the fallback run (collect_plan=False) is empty
        assert result["execution_plan"] == {}
        assert result["plan_xml"] is None
