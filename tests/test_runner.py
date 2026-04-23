# tests/test_runner.py
from unittest.mock import MagicMock, patch

import pytest

from runner import run_query


def _make_conn(messages=None, fetchone_side_effect=None):
    """Zwraca mock połączenia pyodbc z podstawową konfiguracją."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.messages = messages or []
    cursor.nextset.return_value = False
    if fetchone_side_effect is not None:
        cursor.fetchone.side_effect = fetchone_side_effect
    else:
        cursor.fetchone.return_value = None
    return conn, cursor


class TestRunQueryConnectionLifecycle:
    def test_external_conn_is_not_closed(self):
        conn, _ = _make_conn()
        with patch("runner.get_connection") as mock_gc:
            run_query("SELECT 1", conn=conn)
        conn.close.assert_not_called()
        mock_gc.assert_not_called()

    def test_no_conn_creates_and_closes_own_connection(self):
        own_conn, _ = _make_conn()
        with patch("runner.get_connection", return_value=own_conn) as mock_gc:
            run_query("SELECT 1")
        mock_gc.assert_called_once()
        own_conn.close.assert_called_once()

    def test_external_conn_cursor_is_closed(self):
        conn, cursor = _make_conn()
        with patch("runner.get_connection"):
            run_query("SELECT 1", conn=conn)
        cursor.close.assert_called_once()

    def test_own_conn_cursor_is_closed_on_success(self):
        own_conn, cursor = _make_conn()
        with patch("runner.get_connection", return_value=own_conn):
            run_query("SELECT 1")
        cursor.close.assert_called_once()

    def test_own_conn_is_closed_when_query_raises(self):
        own_conn, cursor = _make_conn()
        # DBCC x2, SET STATISTICS IO, SET STATISTICS TIME, actual query (raises)
        cursor.execute.side_effect = [None, None, None, None, Exception("SQL error")]
        with patch("runner.get_connection", return_value=own_conn):
            result = run_query("SELECT 1", collect_plan=False)
        own_conn.close.assert_called_once()
        assert result["error"] == "SQL error"

    def test_external_conn_is_not_closed_when_query_raises(self):
        conn, cursor = _make_conn()
        # DBCC x2, SET STATISTICS IO, SET STATISTICS TIME, actual query (raises)
        cursor.execute.side_effect = [None, None, None, None, Exception("SQL error")]
        with patch("runner.get_connection"):
            result = run_query("SELECT 1", collect_plan=False, conn=conn)
        conn.close.assert_not_called()
        assert result["error"] == "SQL error"


class TestRunQueryReturnFormat:
    def test_success_result_has_required_keys(self):
        conn, _ = _make_conn()
        with patch("runner.get_connection"):
            result = run_query("SELECT 1", collect_plan=False, conn=conn)
        assert "time" in result
        assert "error" in result
        assert "server_metrics" in result
        assert "execution_plan" in result
        assert "plan_xml" in result
        assert "query_store" in result
        assert "warnings" in result

    def test_error_result_has_required_keys(self):
        conn, cursor = _make_conn()
        # DBCC x2, SET STATISTICS IO, SET STATISTICS TIME, actual query (raises)
        cursor.execute.side_effect = [None, None, None, None, Exception("timeout")]
        with patch("runner.get_connection"):
            result = run_query("SELECT 1", collect_plan=False, conn=conn)
        assert result["error"] == "timeout"
        assert result["time"] is None
        assert "server_metrics" in result
        assert "execution_plan" in result
        assert "plan_xml" in result
        assert "query_store" in result
        assert "warnings" in result

    def test_external_conn_result_matches_own_conn_result_structure(self):
        ext_conn, _ = _make_conn()
        own_conn, _ = _make_conn()

        with patch("runner.get_connection"):
            result_ext = run_query("SELECT 1", collect_plan=False, conn=ext_conn)

        with patch("runner.get_connection", return_value=own_conn):
            result_own = run_query("SELECT 1", collect_plan=False)

        assert set(result_ext.keys()) == set(result_own.keys())
