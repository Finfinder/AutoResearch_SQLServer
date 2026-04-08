# tests/test_db.py
import os
from unittest.mock import patch, MagicMock

import pytest

from db import get_connection

_FULL_ENV = {
    "DB_SERVER": "test_server",
    "DB_DATABASE": "test_db",
    "DB_UID": "test_user",
    "DB_PWD": "test_pass",
}


class TestGetConnection:
    def test_missing_all_vars_raises_value_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                get_connection()
        msg = str(exc_info.value)
        assert "DB_SERVER" in msg
        assert "DB_DATABASE" in msg
        assert "DB_UID" in msg
        assert "DB_PWD" in msg

    def test_missing_single_var_raises_value_error(self):
        env_without_pwd = {k: v for k, v in _FULL_ENV.items() if k != "DB_PWD"}
        with patch.dict(os.environ, env_without_pwd, clear=True):
            with pytest.raises(ValueError) as exc_info:
                get_connection()
        assert "DB_PWD" in str(exc_info.value)

    def test_default_driver(self):
        with patch.dict(os.environ, dict(_FULL_ENV), clear=True):
            with patch("db.pyodbc.connect", return_value=MagicMock()) as mock_connect:
                get_connection()
        call_str = mock_connect.call_args[0][0]
        assert "ODBC Driver 17 for SQL Server" in call_str

    def test_custom_driver(self):
        env_with_driver = {**_FULL_ENV, "DB_DRIVER": "Custom Driver"}
        with patch.dict(os.environ, env_with_driver, clear=True):
            with patch("db.pyodbc.connect", return_value=MagicMock()) as mock_connect:
                get_connection()
        call_str = mock_connect.call_args[0][0]
        assert "Custom Driver" in call_str

    def test_connection_string_format(self):
        with patch.dict(os.environ, _FULL_ENV, clear=True):
            with patch("db.pyodbc.connect", return_value=MagicMock()) as mock_connect:
                get_connection()
        call_str = mock_connect.call_args[0][0]
        assert "DRIVER=" in call_str
        assert "SERVER=test_server" in call_str
        assert "DATABASE=test_db" in call_str
        assert "UID=test_user" in call_str
        assert "PWD=test_pass" in call_str
        assert "TrustServerCertificate=yes" in call_str

    def test_returns_connection_object(self):
        mock_conn = MagicMock()
        with patch.dict(os.environ, _FULL_ENV, clear=True):
            with patch("db.pyodbc.connect", return_value=mock_conn):
                result = get_connection()
        assert result is mock_conn
