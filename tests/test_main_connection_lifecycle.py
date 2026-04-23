# tests/test_main_connection_lifecycle.py
from unittest.mock import MagicMock, patch

import pytest

from main import _reset_bench_conn


def _make_args(runs=1):
    args = MagicMock()
    args.runs = runs
    return args


def _success_result():
    return {
        "time": 0.1,
        "error": None,
        "server_metrics": {},
        "execution_plan": {},
        "plan_xml": None,
        "query_store": None,
        "warnings": [],
    }


def _error_result(msg="SQL error"):
    return {
        "time": None,
        "error": msg,
        "server_metrics": {},
        "execution_plan": {},
        "plan_xml": None,
        "query_store": None,
        "warnings": [],
    }


class TestResetBenchConn:
    def test_closes_existing_connection_and_returns_new_one(self):
        old_conn = MagicMock()
        new_conn = MagicMock()
        with patch("main.get_connection", return_value=new_conn):
            result = _reset_bench_conn(old_conn, "test_label")
        old_conn.close.assert_called_once()
        assert result is new_conn

    def test_handles_close_exception_gracefully(self):
        old_conn = MagicMock()
        old_conn.close.side_effect = Exception("close failed")
        new_conn = MagicMock()
        with patch("main.get_connection", return_value=new_conn):
            result = _reset_bench_conn(old_conn)
        assert result is new_conn

    def test_returns_none_when_reconnect_fails(self):
        old_conn = MagicMock()
        with patch("main.get_connection", side_effect=Exception("reconnect failed")):
            result = _reset_bench_conn(old_conn)
        assert result is None

    def test_handles_none_input_without_error(self):
        new_conn = MagicMock()
        with patch("main.get_connection", return_value=new_conn):
            result = _reset_bench_conn(None)
        assert result is new_conn


class TestBenchConnLifecycleInMain:
    def _run_main(self, variants, run_query_side_effect, runs=1, get_conn_side_effect=None):
        bench_conn = MagicMock()
        collected_results = []

        get_conn_values = get_conn_side_effect or [bench_conn]

        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=runs)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=variants), \
             patch("main._compute_base_count", return_value=(None, None)), \
             patch("main.get_connection", side_effect=get_conn_values), \
             patch("main.run_query", side_effect=run_query_side_effect), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main._check_row_count", return_value=(None, False)), \
             patch("main.save_results", side_effect=lambda r: collected_results.extend(r)), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        return bench_conn, collected_results

    def test_bench_conn_closed_in_finally_on_success(self):
        bench_conn, _ = self._run_main(
            variants=[("base", "SELECT 1")],
            run_query_side_effect=[_success_result()],
        )
        bench_conn.close.assert_called_once()

    def test_bench_conn_closed_in_finally_on_exception(self):
        bench_conn = MagicMock()
        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=1)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1")]), \
             patch("main._compute_base_count", return_value=(None, None)), \
             patch("main.get_connection", return_value=bench_conn), \
             patch("main._check_guardrails", side_effect=RuntimeError("unexpected")), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            with pytest.raises(RuntimeError, match="unexpected"):
                main()
        bench_conn.close.assert_called_once()

    def test_bench_conn_reset_after_single_run_error(self):
        bench_conn = MagicMock()
        new_bench_conn = MagicMock()

        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=1)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1"), ("v2", "SELECT 2")]), \
             patch("main._compute_base_count", return_value=(None, None)), \
             patch("main.get_connection", side_effect=[bench_conn, new_bench_conn]), \
             patch("main.run_query", side_effect=[_error_result(), _success_result()]), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main._check_row_count", return_value=(None, False)), \
             patch("main.save_results"), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        # original conn closed by _reset_bench_conn
        bench_conn.close.assert_called()
        # new conn closed in finally
        new_bench_conn.close.assert_called_once()

    def test_benchmark_continues_after_single_run_error(self):
        bench_conn = MagicMock()
        new_bench_conn = MagicMock()
        collected_results = []

        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=1)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1"), ("v2", "SELECT 2")]), \
             patch("main._compute_base_count", return_value=(None, None)), \
             patch("main.get_connection", side_effect=[bench_conn, new_bench_conn]), \
             patch("main.run_query", side_effect=[_error_result(), _success_result()]), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main._check_row_count", return_value=(None, False)), \
             patch("main.save_results", side_effect=lambda r: collected_results.extend(r)), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        assert len(collected_results) == 1
        assert collected_results[0]["label"] == "v2"

    def test_bench_conn_reset_after_multi_run_error(self):
        bench_conn = MagicMock()
        new_bench_conn = MagicMock()

        # run 1 of v1: error → reset; run 2 of v1: success on new conn
        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=2)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1")]), \
             patch("main._compute_base_count", return_value=(None, None)), \
             patch("main.get_connection", side_effect=[bench_conn, new_bench_conn]), \
             patch("main.run_query", side_effect=[_error_result(), _success_result()]), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main._check_row_count", return_value=(None, False)), \
             patch("main.save_results"), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        bench_conn.close.assert_called()
        new_bench_conn.close.assert_called_once()

    def test_no_unclosed_connections_after_main(self):
        bench_conn = MagicMock()
        bench_conn.close.return_value = None

        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=1)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1")]), \
             patch("main._compute_base_count", return_value=(None, None)), \
             patch("main.get_connection", return_value=bench_conn), \
             patch("main.run_query", return_value=_success_result()), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main._check_row_count", return_value=(None, False)), \
             patch("main.save_results"), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        assert bench_conn.close.call_count == 1

    def test_benchmark_continues_when_reconnect_fails(self):
        bench_conn = MagicMock()
        collected_results = []

        # First get_connection: bench_conn; second (in _reset_bench_conn): raises
        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=1)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1"), ("v2", "SELECT 2")]), \
             patch("main._compute_base_count", return_value=(None, None)), \
             patch("main.get_connection", side_effect=[bench_conn, Exception("reconnect failed")]), \
             patch("main.run_query", side_effect=[_error_result(), _success_result()]), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main._check_row_count", return_value=(None, False)), \
             patch("main.save_results", side_effect=lambda r: collected_results.extend(r)), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        # v1 errored and reset failed (bench_conn=None), but v2 still processed
        assert len(collected_results) == 1
        assert collected_results[0]["label"] == "v2"
