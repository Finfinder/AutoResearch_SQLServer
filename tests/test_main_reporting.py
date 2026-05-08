from unittest.mock import patch

from main import _print_ranking, _print_variant_result


def _printed_lines(mock_print):
    return [call.args[0] for call in mock_print.call_args_list]


def _single_run_result():
    return {
        "time": 0.1234,
        "warnings": ["Query Store unavailable"],
        "server_metrics": {
            "cpu_time_ms": 11,
            "elapsed_time_ms": 15,
            "logical_reads": 42,
            "physical_reads": 2,
        },
        "execution_plan": {
            "memory_grant_kb": 512,
            "spill_warnings": [{"SpillLevel": "1"}],
        },
    }


def _aggregated_run_result():
    return {
        "time": {"mean": 0.1234, "median": 0.1200, "stdev": 0.0100},
        "warnings": ["Execution plan unavailable"],
        "server_metrics": {
            "cpu_time_ms": {"mean": 10.0, "median": 9.0, "stdev": 1.0},
            "logical_reads": {"mean": 50.0, "median": 45.0, "stdev": 5.0},
            "physical_reads": {"mean": 4.0, "median": 3.0, "stdev": 1.0},
        },
        "execution_plan": {
            "memory_grant_kb": 256,
            "spill_warnings": [{"SpillLevel": "2"}],
        },
    }


class TestPrintVariantResult:
    def test_single_run_prints_metrics_and_warnings(self):
        result = _single_run_result()

        with patch("builtins.print") as mock_print, \
             patch("main.result_logger.info"), \
             patch("main.logger.warning") as mock_warning:
            _print_variant_result(result, 1, 2, "single", num_runs=1)

        printed = _printed_lines(mock_print)
        assert "Test 1/2 [single]" in printed
        assert any("Time: 0.1234s (server: 11ms CPU / 15ms elapsed)" in line for line in printed)
        assert "📊 IO: 42 logical reads, 2 physical reads" in printed
        assert "💾 Memory grant: 512 KB" in printed
        assert "⚠️  SpillToTempDb detected!" in printed
        assert "⚠️  Query Store unavailable" in printed
        mock_warning.assert_any_call("SpillToTempDb detected in [%s]", "single")
        mock_warning.assert_any_call("[%s]: %s", "single", "Query Store unavailable")

    def test_aggregated_run_prints_metrics_and_warnings(self):
        result = _aggregated_run_result()

        with patch("builtins.print") as mock_print, \
             patch("main.result_logger.info"), \
             patch("main.logger.warning") as mock_warning:
            _print_variant_result(result, 2, 4, "agg", num_runs=3)

        printed = _printed_lines(mock_print)
        assert "Test 2/4 [agg] (3 runs)" in printed
        assert any("Time: 0.1234s mean" in line for line in printed)
        assert any("CPU: 10ms mean" in line for line in printed)
        assert "📊 IO: 45 logical reads (median), 3 physical reads (median)" in printed
        assert "💾 Memory grant: 256 KB" in printed
        assert "⚠️  SpillToTempDb detected!" in printed
        assert "⚠️  Execution plan unavailable" in printed
        mock_warning.assert_any_call("SpillToTempDb detected in [%s]", "agg")
        mock_warning.assert_any_call("[%s]: %s", "agg", "Execution plan unavailable")


class TestPrintRanking:
    def test_single_run_ranking_prints_best_results_and_spill_summary(self):
        results = [
            {
                "label": "slow",
                "time": 0.3,
                "server_metrics": {"logical_reads": 30, "cpu_time_ms": 20},
                "execution_plan": {"memory_grant_kb": 400, "spill_warnings": []},
            },
            {
                "label": "fast",
                "time": 0.1,
                "server_metrics": {"logical_reads": 10, "cpu_time_ms": 5},
                "execution_plan": {"memory_grant_kb": 100, "spill_warnings": [{"SpillLevel": "1"}]},
            },
        ]

        with patch("builtins.print") as mock_print, \
             patch("main.result_logger.info"), \
             patch("main.logger.warning") as mock_warning:
            _print_ranking(results, num_runs=1)

        printed = _printed_lines(mock_print)
        assert "\n🏆 RANKING:" in printed
        assert "  ⏱️  Best by time:         [fast] — 0.1000s" in printed
        assert "  📊 Best by IO:            [fast] — 10 logical reads" in printed
        assert "  ⚡ Best by CPU:            [fast] — 5ms" in printed
        assert "  💾 Best by memory grant:  [fast] — 100 KB" in printed
        assert "  ⚠️  SpillToTempDb:         [fast]" in printed
        mock_warning.assert_called_once_with("SpillToTempDb in ranking: %s", "[fast]")

    def test_aggregated_ranking_uses_median_values(self):
        results = [
            {
                "label": "slow",
                "time": {"median": 0.4},
                "server_metrics": {
                    "logical_reads": {"median": 40},
                    "cpu_time_ms": {"median": 16},
                },
                "execution_plan": {"memory_grant_kb": 300, "spill_warnings": []},
            },
            {
                "label": "fast",
                "time": {"median": 0.2},
                "server_metrics": {
                    "logical_reads": {"median": 20},
                    "cpu_time_ms": {"median": 8},
                },
                "execution_plan": {"memory_grant_kb": 120, "spill_warnings": []},
            },
        ]

        with patch("builtins.print") as mock_print, \
             patch("main.result_logger.info"), \
             patch("main.logger.warning"):
            _print_ranking(results, num_runs=3)

        printed = _printed_lines(mock_print)
        assert "  ⏱️  Best by time (median):  [fast] — 0.2000s" in printed
        assert "  📊 Best by IO (median):     [fast] — 20 logical reads" in printed
        assert "  ⚡ Best by CPU (median):    [fast] — 8ms" in printed
        assert "  💾 Best by memory grant:  [fast] — 120 KB" in printed