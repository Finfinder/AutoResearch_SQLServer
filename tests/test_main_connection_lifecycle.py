# tests/test_main_connection_lifecycle.py
from unittest.mock import MagicMock, patch

import pytest

from main import _build_base_strict_context, _reset_bench_conn, _resolve_strict_validation_source
from validator import ValidationResult


def _make_args(runs=1, strict_validation=False):
    args = MagicMock()
    args.runs = runs
    args.strict_validation = strict_validation
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


class TestStrictValidationPolicy:
    def test_cli_flag_has_priority_over_row_threshold(self):
        assert _resolve_strict_validation_source(10_000, True) == "cli"

    def test_auto_strict_is_enabled_below_threshold(self):
        assert _resolve_strict_validation_source(199, False) == "auto"

    def test_auto_strict_is_disabled_at_threshold_and_above(self):
        assert _resolve_strict_validation_source(200, False) is None
        assert _resolve_strict_validation_source(500, False) is None


class TestBuildBaseStrictContext:
    def test_enables_auto_strict_for_small_results(self):
        val_conn = MagicMock()
        strict_context = {
            "ordered": True,
            "base_signature": "sig",
            "base_row_count": 10,
            "fallback_reason": None,
            "warnings": [],
        }

        with patch("main.build_strict_validation_context", return_value=strict_context):
            base_count, context, source = _build_base_strict_context("SELECT 1 ORDER BY 1", 10, val_conn, False)

        assert base_count == 10
        assert context == strict_context
        assert source == "auto"

    def test_skips_auto_strict_at_threshold_or_above(self):
        val_conn = MagicMock()

        with patch("main.build_strict_validation_context") as build_context:
            base_count, context, source = _build_base_strict_context("SELECT 1", 200, val_conn, False)

        assert base_count == 200
        assert context is None
        assert source is None
        build_context.assert_not_called()

    def test_recovers_base_count_for_cli_strict_when_count_is_unavailable(self):
        val_conn = MagicMock()
        strict_context = {
            "ordered": True,
            "base_signature": "sig",
            "base_row_count": 12,
            "fallback_reason": None,
            "warnings": [],
        }

        with patch("main.build_strict_validation_context", return_value=strict_context):
            base_count, context, source = _build_base_strict_context("SELECT 1 ORDER BY 1", None, val_conn, True)

        assert base_count == 12
        assert context == strict_context
        assert source == "cli"

    def test_preserves_strict_source_when_validation_connection_is_unavailable(self):
        base_count, context, source = _build_base_strict_context("SELECT 1", None, None, True)

        assert base_count is None
        assert source == "cli"
        assert context["base_signature"] is None
        assert context["fallback_reason"] == "Validation connection unavailable"


class TestBenchConnLifecycleInMain:
    def _run_main(
        self,
        variants,
        run_query_side_effect,
        runs=1,
        get_conn_side_effect=None,
        guardrail_result=([], False),
        row_count_result=(None, False),
    ):
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
             patch("main._check_guardrails", return_value=guardrail_result), \
             patch("main._check_row_count", return_value=row_count_result), \
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

    def test_single_run_collects_expected_json_result(self):
        _, collected_results = self._run_main(
            variants=[("base", "SELECT 1")],
            run_query_side_effect=[_success_result()],
            runs=1,
        )

        assert len(collected_results) == 1
        result = collected_results[0]
        assert result["label"] == "base"
        assert result["query"] == "SELECT 1"
        assert result["time"] == pytest.approx(0.1)
        assert result["guardrail_warnings"] == []
        assert result["warnings"] == []
        assert result["validation"] is None
        assert result["execution_plan_file"] is None

    def test_single_run_preserves_warning_merge_order(self):
        result_with_warning = _success_result()
        result_with_warning["warnings"] = ["runtime warning"]

        _, collected_results = self._run_main(
            variants=[("base", "SELECT 1")],
            run_query_side_effect=[result_with_warning],
            runs=1,
            guardrail_result=(["guardrail warning"], False),
        )

        assert collected_results[0]["warnings"] == ["runtime warning", "guardrail warning"]

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

    def test_multi_run_collects_expected_json_result(self):
        _, collected_results = self._run_main(
            variants=[("v1", "SELECT 1")],
            run_query_side_effect=[_success_result(), _success_result()],
            runs=2,
        )

        assert len(collected_results) == 1
        result = collected_results[0]
        assert result["label"] == "v1"
        assert result["query"] == "SELECT 1"
        assert result["runs"] == 2
        assert result["guardrail_warnings"] == []
        assert result["warnings"] == []
        assert result["validation"] is None
        assert result["execution_plan_file"] is None
        assert isinstance(result["time"], dict)
        assert result["time"]["median"] == pytest.approx(0.1)
        assert len(result["raw_runs"]) == 2
        assert result["raw_runs"][0]["time"] == pytest.approx(0.1)
        assert result["raw_runs"][1]["time"] == pytest.approx(0.1)

    def test_multi_run_preserves_warning_merge_order(self):
        first_run = _success_result()
        first_run["warnings"] = ["aggregate warning"]

        _, collected_results = self._run_main(
            variants=[("v1", "SELECT 1")],
            run_query_side_effect=[first_run, _success_result()],
            runs=2,
            guardrail_result=(["guardrail warning"], False),
        )

        assert collected_results[0]["warnings"] == ["aggregate warning", "guardrail warning"]

    def test_single_run_preserves_strict_fallback_metadata_in_json_result(self):
        validation_info = {
            "is_valid": True,
            "base_count": 10,
            "variant_count": 10,
            "message": "Strict validation skipped — unsupported SQL type; row count OK",
            "mode": "row_count",
            "ordered": False,
            "strict_requested": True,
            "strict_applied": False,
            "strict_source": "auto",
            "fallback_reason": "unsupported SQL type",
            "warnings": ["Unsupported SQL type for strict validation: text"],
        }

        _, collected_results = self._run_main(
            variants=[("base", "SELECT 1")],
            run_query_side_effect=[_success_result()],
            runs=1,
            row_count_result=(validation_info, False),
        )

        assert collected_results[0]["validation"]["mode"] == "row_count"
        assert collected_results[0]["validation"]["strict_requested"] is True
        assert collected_results[0]["validation"]["strict_applied"] is False
        assert collected_results[0]["validation"]["strict_source"] == "auto"
        assert collected_results[0]["validation"]["fallback_reason"] == "unsupported SQL type"

    def test_multi_run_preserves_strict_fallback_metadata_in_json_result(self):
        validation_info = {
            "is_valid": True,
            "base_count": 10,
            "variant_count": 10,
            "message": "Strict validation skipped — unsupported SQL type; row count OK",
            "mode": "row_count",
            "ordered": False,
            "strict_requested": True,
            "strict_applied": False,
            "strict_source": "cli",
            "fallback_reason": "unsupported SQL type",
            "warnings": ["Unsupported SQL type for strict validation: text"],
        }

        _, collected_results = self._run_main(
            variants=[("v1", "SELECT 1")],
            run_query_side_effect=[_success_result(), _success_result()],
            runs=2,
            row_count_result=(validation_info, False),
        )

        assert collected_results[0]["validation"]["mode"] == "row_count"
        assert collected_results[0]["validation"]["strict_requested"] is True
        assert collected_results[0]["validation"]["strict_applied"] is False
        assert collected_results[0]["validation"]["strict_source"] == "cli"
        assert collected_results[0]["validation"]["fallback_reason"] == "unsupported SQL type"

    def test_main_passes_cli_strict_context_to_validation(self):
        bench_conn = MagicMock()
        val_conn = MagicMock()
        strict_context = {
            "ordered": False,
            "base_signature": "abc",
            "base_row_count": 12,
            "fallback_reason": None,
            "warnings": [],
        }
        collected_results = []
        check_row_count = MagicMock(
            return_value=(
                {
                    "is_valid": True,
                    "base_count": 12,
                    "variant_count": 12,
                    "message": "OK",
                    "mode": "strict_hash",
                    "ordered": False,
                    "strict_requested": True,
                    "strict_applied": True,
                    "strict_source": "cli",
                    "fallback_reason": None,
                    "warnings": [],
                },
                False,
            )
        )

        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=1, strict_validation=True)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1")]), \
             patch("main._compute_base_count", return_value=(None, val_conn)), \
             patch("main._build_base_strict_context", return_value=(12, strict_context, "cli")), \
             patch("main.get_connection", return_value=bench_conn), \
             patch("main.run_query", return_value=_success_result()), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main._check_row_count", check_row_count), \
             patch("main.save_results", side_effect=lambda r: collected_results.extend(r)), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        check_row_count.assert_called_once_with(12, "SELECT 1", "v1", val_conn, strict_context, "cli")
        assert collected_results[0]["validation"]["mode"] == "strict_hash"
        assert collected_results[0]["validation"]["strict_source"] == "cli"

    def test_main_passes_auto_strict_context_to_validation(self):
        bench_conn = MagicMock()
        val_conn = MagicMock()
        strict_context = {
            "ordered": False,
            "base_signature": "abc",
            "base_row_count": 42,
            "fallback_reason": None,
            "warnings": [],
        }
        check_row_count = MagicMock(return_value=(None, False))

        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=1, strict_validation=False)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1")]), \
             patch("main._compute_base_count", return_value=(42, val_conn)), \
             patch("main._build_base_strict_context", return_value=(42, strict_context, "auto")), \
             patch("main.get_connection", return_value=bench_conn), \
             patch("main.run_query", return_value=_success_result()), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main._check_row_count", check_row_count), \
             patch("main.save_results"), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        check_row_count.assert_called_once_with(42, "SELECT 1", "v1", val_conn, strict_context, "auto")

    def test_main_uses_row_count_path_for_large_results_without_cli(self):
        bench_conn = MagicMock()
        val_conn = MagicMock()
        check_row_count = MagicMock(return_value=(None, False))

        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=1, strict_validation=False)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1")]), \
             patch("main._compute_base_count", return_value=(250, val_conn)), \
             patch("main._build_base_strict_context", return_value=(250, None, None)), \
             patch("main.get_connection", return_value=bench_conn), \
             patch("main.run_query", return_value=_success_result()), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main._check_row_count", check_row_count), \
             patch("main.save_results"), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        check_row_count.assert_called_once_with(250, "SELECT 1", "v1", val_conn, None, None)

    def test_main_preserves_validation_metadata_when_cli_strict_has_no_base_count(self):
        bench_conn = MagicMock()
        val_conn = MagicMock()
        strict_context = {
            "ordered": False,
            "base_signature": None,
            "base_row_count": None,
            "fallback_reason": "Strict validation context is unavailable",
            "warnings": ["Strict validation setup failed"],
        }
        collected_results = []
        validation_result = ValidationResult(
            is_valid=True,
            base_count=None,
            variant_count=-1,
            message=(
                "Strict validation skipped — Strict validation context is unavailable; "
                "base row count unavailable"
            ),
            mode="row_count",
            ordered=False,
            strict_requested=True,
            strict_applied=False,
            strict_source="cli",
            fallback_reason="Strict validation context is unavailable",
            warnings=["Strict validation setup failed"],
        )

        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=1, strict_validation=True)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1")]), \
             patch("main._compute_base_count", return_value=(None, val_conn)), \
             patch("main._build_base_strict_context", return_value=(None, strict_context, "cli")), \
             patch("main.get_connection", return_value=bench_conn), \
             patch("main.run_query", return_value=_success_result()), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main.validate_query_results", return_value=validation_result), \
             patch("main.save_results", side_effect=lambda r: collected_results.extend(r)), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        assert collected_results[0]["validation"]["strict_requested"] is True
        assert collected_results[0]["validation"]["strict_applied"] is False
        assert collected_results[0]["validation"]["strict_source"] == "cli"
        assert collected_results[0]["validation"]["fallback_reason"] == "Strict validation context is unavailable"

    def test_main_preserves_validation_metadata_when_validation_connection_is_unavailable(self):
        bench_conn = MagicMock()
        collected_results = []

        with patch("main.setup_logging"), \
             patch("main.parse_args", return_value=_make_args(runs=1, strict_validation=True)), \
             patch("main.load_query", return_value="SELECT 1"), \
             patch("main.generate_variants", return_value=[("v1", "SELECT 1")]), \
             patch("main._compute_base_count", return_value=(None, None)), \
             patch(
                 "main._build_base_strict_context",
                 return_value=(
                     None,
                     {
                         "ordered": False,
                         "base_signature": None,
                         "base_row_count": None,
                         "fallback_reason": "Validation connection unavailable",
                         "warnings": ["Validation connection unavailable"],
                     },
                     "cli",
                 ),
             ), \
             patch("main.get_connection", return_value=bench_conn), \
             patch("main.run_query", return_value=_success_result()), \
             patch("main._check_guardrails", return_value=([], False)), \
             patch("main.save_results", side_effect=lambda r: collected_results.extend(r)), \
             patch("main._print_ranking"), \
             patch("main._print_variant_result"):
            from main import main
            main()

        assert collected_results[0]["validation"]["strict_requested"] is True
        assert collected_results[0]["validation"]["strict_applied"] is False
        assert collected_results[0]["validation"]["strict_source"] == "cli"
        assert collected_results[0]["validation"]["fallback_reason"] == "Validation connection unavailable"

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
