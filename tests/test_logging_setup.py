# tests/test_logging_setup.py
import logging
import os
import unittest
from unittest.mock import patch
import colorlog


class TestSetupLogging(unittest.TestCase):
    def _call_setup_logging(self, env_overrides=None):
        # Import inside the test to allow patching os.environ cleanly.
        # We must reload main to avoid state from a previous call.
        import importlib
        import main as main_module
        importlib.reload(main_module)

        env = {
            "DB_SERVER": "localhost",
            "DB_DATABASE": "test",
            "DB_UID": "user",
            "DB_PWD": "pass",
        }
        if env_overrides:
            env.update(env_overrides)

        with patch.dict(os.environ, env, clear=True):
            with patch("main.load_dotenv"):
                root = logging.getLogger()
                original_handlers = root.handlers[:]
                try:
                    main_module.setup_logging()
                    return main_module, root
                finally:
                    # Restore root logger to avoid leaking handlers between tests.
                    for h in root.handlers[:]:
                        if h not in original_handlers:
                            root.removeHandler(h)
                            if hasattr(h, "close"):
                                h.close()

    def test_root_logger_has_stream_and_file_handler(self):
        import importlib
        import main as main_module
        importlib.reload(main_module)

        with patch.dict(os.environ, {"LOG_LEVEL": "INFO"}, clear=False):
            with patch("main.load_dotenv"):
                with patch("main.logging.FileHandler") as mock_fh_cls:
                    mock_fh_cls.return_value = logging.NullHandler()
                    with patch("main.Path.mkdir"):
                        root = logging.getLogger()
                        handlers_before = set(root.handlers)
                        main_module.setup_logging()
                        new_handlers = [h for h in root.handlers if h not in handlers_before]
                        try:
                            handler_types = {type(h).__name__ for h in new_handlers}
                            self.assertIn("StreamHandler", handler_types)
                        finally:
                            for h in new_handlers:
                                root.removeHandler(h)
                                if hasattr(h, "close"):
                                    h.close()

    def test_default_log_level_is_info(self):
        import importlib
        import main as main_module
        importlib.reload(main_module)

        env = {k: v for k, v in os.environ.items() if k != "LOG_LEVEL"}
        with patch.dict(os.environ, env, clear=True):
            with patch("main.load_dotenv"):
                with patch("main.logging.FileHandler") as mock_fh_cls:
                    mock_fh_cls.return_value = logging.NullHandler()
                    with patch("main.Path.mkdir"):
                        root = logging.getLogger()
                        handlers_before = set(root.handlers)
                        main_module.setup_logging()
                        new_handlers = [h for h in root.handlers if h not in handlers_before]
                        try:
                            stream_handlers = [
                                h for h in new_handlers
                                if type(h).__name__ == "StreamHandler"
                            ]
                            self.assertGreaterEqual(len(stream_handlers), 1)
                            self.assertEqual(stream_handlers[0].level, logging.INFO)
                        finally:
                            for h in new_handlers:
                                root.removeHandler(h)
                                if hasattr(h, "close"):
                                    h.close()

    def test_log_level_debug_changes_stream_handler_level(self):
        import importlib
        import main as main_module
        importlib.reload(main_module)

        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}, clear=False):
            with patch("main.load_dotenv"):
                with patch("main.logging.FileHandler") as mock_fh_cls:
                    mock_fh_cls.return_value = logging.NullHandler()
                    with patch("main.Path.mkdir"):
                        root = logging.getLogger()
                        handlers_before = set(root.handlers)
                        main_module.setup_logging()
                        new_handlers = [h for h in root.handlers if h not in handlers_before]
                        try:
                            stream_handlers = [
                                h for h in new_handlers
                                if type(h).__name__ == "StreamHandler"
                            ]
                            self.assertGreaterEqual(len(stream_handlers), 1)
                            self.assertEqual(stream_handlers[0].level, logging.DEBUG)
                        finally:
                            for h in new_handlers:
                                root.removeHandler(h)
                                if hasattr(h, "close"):
                                    h.close()

    def test_invalid_log_level_falls_back_to_info(self):
        import importlib
        import main as main_module
        importlib.reload(main_module)

        with patch.dict(os.environ, {"LOG_LEVEL": "VERBOSE"}, clear=False):
            with patch("main.load_dotenv"):
                with patch("main.logging.FileHandler") as mock_fh_cls:
                    mock_fh_cls.return_value = logging.NullHandler()
                    with patch("main.Path.mkdir"):
                        root = logging.getLogger()
                        handlers_before = set(root.handlers)
                        main_module.setup_logging()
                        new_handlers = [h for h in root.handlers if h not in handlers_before]
                        try:
                            stream_handlers = [
                                h for h in new_handlers
                                if type(h).__name__ == "StreamHandler"
                            ]
                            self.assertGreaterEqual(len(stream_handlers), 1)
                            self.assertEqual(stream_handlers[0].level, logging.INFO)
                        finally:
                            for h in new_handlers:
                                root.removeHandler(h)
                                if hasattr(h, "close"):
                                    h.close()

    def test_benchmark_logger_has_propagate_false(self):
        import importlib
        import main as main_module
        importlib.reload(main_module)

        with patch.dict(os.environ, {}, clear=False):
            with patch("main.load_dotenv"):
                with patch("main.logging.FileHandler") as mock_fh_cls:
                    mock_fh_cls.return_value = logging.NullHandler()
                    with patch("main.Path.mkdir"):
                        root = logging.getLogger()
                        handlers_before = set(root.handlers)
                        main_module.setup_logging()
                        new_handlers = [h for h in root.handlers if h not in handlers_before]
                        try:
                            bench = logging.getLogger("benchmark")
                            self.assertFalse(bench.propagate)
                        finally:
                            for h in new_handlers:
                                root.removeHandler(h)
                                if hasattr(h, "close"):
                                    h.close()
                            bench = logging.getLogger("benchmark")
                            bench.propagate = True
                            for h in bench.handlers[:]:
                                bench.removeHandler(h)

    def _setup_logging_with_mocks(self, isatty_return, env_overrides=None):
        import importlib
        import main as main_module
        importlib.reload(main_module)

        env = {k: v for k, v in os.environ.items() if k not in ("LOG_LEVEL", "NO_COLOR")}
        if env_overrides:
            env.update(env_overrides)

        with patch.dict(os.environ, env, clear=True):
            with patch("main.load_dotenv"):
                with patch("main.logging.FileHandler") as mock_fh_cls:
                    mock_fh_cls.return_value = logging.NullHandler()
                    with patch("main.Path.mkdir"):
                        with patch("main.sys.stderr") as mock_stderr:
                            mock_stderr.isatty.return_value = isatty_return
                            root = logging.getLogger()
                            handlers_before = set(root.handlers)
                            main_module.setup_logging()
                            new_handlers = [h for h in root.handlers if h not in handlers_before]
                            try:
                                stream_handlers = [
                                    h for h in new_handlers
                                    if type(h).__name__ == "StreamHandler"
                                ]
                                return stream_handlers
                            finally:
                                for h in new_handlers:
                                    root.removeHandler(h)
                                    if hasattr(h, "close"):
                                        h.close()

    def test_stderr_handler_uses_colored_formatter_when_tty(self):
        stream_handlers = self._setup_logging_with_mocks(isatty_return=True)
        self.assertGreaterEqual(len(stream_handlers), 1)
        self.assertIsInstance(stream_handlers[0].formatter, colorlog.ColoredFormatter)

    def test_stderr_handler_uses_plain_formatter_when_no_color(self):
        stream_handlers = self._setup_logging_with_mocks(
            isatty_return=True, env_overrides={"NO_COLOR": "1"}
        )
        self.assertGreaterEqual(len(stream_handlers), 1)
        self.assertNotIsInstance(stream_handlers[0].formatter, colorlog.ColoredFormatter)
        self.assertIsInstance(stream_handlers[0].formatter, logging.Formatter)

    def test_stderr_handler_uses_plain_formatter_when_not_tty(self):
        stream_handlers = self._setup_logging_with_mocks(isatty_return=False)
        self.assertGreaterEqual(len(stream_handlers), 1)
        self.assertNotIsInstance(stream_handlers[0].formatter, colorlog.ColoredFormatter)
        self.assertIsInstance(stream_handlers[0].formatter, logging.Formatter)

    def test_file_handler_always_uses_plain_formatter(self):
        import importlib
        import main as main_module
        importlib.reload(main_module)

        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}, clear=False):
            with patch("main.load_dotenv"):
                with patch("main.sys.stderr") as mock_stderr:
                    mock_stderr.isatty.return_value = True
                    captured_file_handler = []

                    class TrackingFileHandler(logging.NullHandler):
                        def __init__(self, *args, **kwargs):
                            super().__init__()
                            captured_file_handler.append(self)

                    with patch("main.logging.FileHandler", TrackingFileHandler):
                        with patch("main.Path.mkdir"):
                            root = logging.getLogger()
                            handlers_before = set(root.handlers)
                            main_module.setup_logging()
                            new_handlers = [h for h in root.handlers if h not in handlers_before]
                            try:
                                self.assertGreaterEqual(len(captured_file_handler), 1)
                                fh = captured_file_handler[0]
                                self.assertNotIsInstance(fh.formatter, colorlog.ColoredFormatter)
                                self.assertIsInstance(fh.formatter, logging.Formatter)
                            finally:
                                for h in new_handlers:
                                    root.removeHandler(h)
                                    if hasattr(h, "close"):
                                        h.close()
                                bench = logging.getLogger("benchmark")
                                bench.propagate = True
                                for h in bench.handlers[:]:
                                    bench.removeHandler(h)
