"""
Tests for new packaging code paths:
- app._get_db_path() lazy resolver
- app._get_default_folders() lazy resolver
- app._merge_into_session() extracted helper
- finn_tracker.__main__ CLI functions
"""
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import app as flask_app
from finn_tracker.__main__ import (
    _check_python_version,
    _resolve_data_dir,
    _check_port,
)


# ── app._get_db_path() ────────────────────────────────────────────────────────

class TestGetDbPath(unittest.TestCase):

    def setUp(self):
        self._orig = flask_app.DB_PATH

    def tearDown(self):
        flask_app.DB_PATH = self._orig

    def test_sentinel_set_returns_sentinel(self):
        flask_app.DB_PATH = Path("/tmp/test_sentinel.db")
        self.assertEqual(flask_app._get_db_path(), Path("/tmp/test_sentinel.db"))

    def test_sentinel_none_uses_env_var(self):
        flask_app.DB_PATH = None
        with patch.dict(os.environ, {"EXPENSE_TRACKER_DATA": "/tmp/et_test"}):
            result = flask_app._get_db_path()
        self.assertEqual(result, Path("/tmp/et_test/finn_tracker.db"))

    def test_sentinel_none_no_env_uses_default(self):
        flask_app.DB_PATH = None
        env = {k: v for k, v in os.environ.items() if k != "EXPENSE_TRACKER_DATA"}
        with patch.dict(os.environ, env, clear=True):
            result = flask_app._get_db_path()
        self.assertTrue(str(result).endswith("finn_tracker.db"))


# ── app._get_default_folders() ────────────────────────────────────────────────

class TestGetDefaultFolders(unittest.TestCase):

    def test_returns_expense_and_income(self):
        with patch.dict(os.environ, {"EXPENSE_TRACKER_DATA": "/tmp/finn_test"}):
            folders = flask_app._get_default_folders()
        self.assertEqual(len(folders), 2)
        names = {f.name for f in folders}
        self.assertIn("expense", names)
        self.assertIn("income", names)

    def test_resolves_from_env_var(self):
        with patch.dict(os.environ, {"EXPENSE_TRACKER_DATA": "/tmp/finn_test"}):
            folders = flask_app._get_default_folders()
        self.assertTrue(all(str(f).startswith("/tmp/finn_test") for f in folders))


# ── app._merge_into_session() ─────────────────────────────────────────────────

class TestMergeIntoSession(unittest.TestCase):

    def setUp(self):
        self._orig_session = dict(flask_app._session)
        flask_app._session["user_transactions"] = []

    def tearDown(self):
        flask_app._session.update(self._orig_session)

    def _make_txn(self, date="2024-01-01", merchant="Test", amount=-10.0):
        return {"date": date, "merchant": merchant, "amount": amount,
                "txn_id": "abc123", "category": "Uncategorized"}

    def test_adds_new_transaction(self):
        txn = self._make_txn()
        with patch.object(flask_app, "_db_save_user_transactions"):
            result = flask_app._merge_into_session([txn])
        self.assertEqual(len(result), 1)
        self.assertEqual(len(flask_app._session["user_transactions"]), 1)

    def test_deduplicates_against_existing(self):
        txn = self._make_txn()
        flask_app._session["user_transactions"] = [txn]
        with patch.object(flask_app, "_db_save_user_transactions"):
            result = flask_app._merge_into_session([txn])
        self.assertEqual(len(result), 0)
        self.assertEqual(len(flask_app._session["user_transactions"]), 1)

    def test_case_insensitive_merchant_dedup(self):
        txn = self._make_txn(merchant="Whole Foods")
        flask_app._session["user_transactions"] = [txn]
        dup = self._make_txn(merchant="WHOLE FOODS")
        with patch.object(flask_app, "_db_save_user_transactions"):
            result = flask_app._merge_into_session([dup])
        self.assertEqual(len(result), 0)

    def test_persists_new_transactions(self):
        txn = self._make_txn()
        saved = []
        with patch.object(flask_app, "_db_save_user_transactions", side_effect=lambda x: saved.extend(x)):
            flask_app._merge_into_session([txn])
        self.assertEqual(len(saved), 1)


# ── finn_tracker.__main__ functions ────────────────────────────────────────

class TestCheckPythonVersion(unittest.TestCase):

    def test_passes_on_current_version(self):
        # Should not raise — we're running on a supported version
        try:
            _check_python_version()
        except SystemExit:
            self.fail("_check_python_version() raised SystemExit on current Python")

    def test_exits_on_old_version(self):
        with patch.object(sys, "version_info", (3, 8, 0, "final", 0)):
            with self.assertRaises(SystemExit):
                _check_python_version()


class TestResolveDataDir(unittest.TestCase):

    def test_uses_env_var_when_set(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"EXPENSE_TRACKER_DATA": tmp}):
                result = _resolve_data_dir()
            self.assertEqual(result, Path(tmp).resolve())

    def test_defaults_to_documents_finn_tracker(self):
        env = {k: v for k, v in os.environ.items() if k != "EXPENSE_TRACKER_DATA"}
        with patch.dict(os.environ, env, clear=True):
            with patch("pathlib.Path.mkdir"):  # don't actually create dirs
                result = _resolve_data_dir()
        self.assertTrue(str(result).endswith("finn-tracker"))

    def test_creates_expense_and_income_subdirs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"EXPENSE_TRACKER_DATA": tmp}):
                result = _resolve_data_dir()
            self.assertTrue((result / "expense").exists())
            self.assertTrue((result / "income").exists())

    def test_exits_on_permission_error(self):
        with patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
            with patch.dict(os.environ, {"EXPENSE_TRACKER_DATA": "/no/such/path"}):
                with self.assertRaises(SystemExit):
                    _resolve_data_dir()


class TestCheckPort(unittest.TestCase):

    def test_free_port_returns_true(self):
        # Port 0 = OS assigns a free port, so connect to it should fail
        with patch("socket.socket") as mock_sock:
            mock_sock.return_value.__enter__ = MagicMock(return_value=MagicMock(connect_ex=MagicMock(return_value=1)))
            mock_sock.return_value.__exit__ = MagicMock(return_value=False)
            self.assertTrue(_check_port(19999))

    def test_in_use_port_returns_false(self):
        with patch("socket.socket") as mock_sock:
            mock_sock.return_value.__enter__ = MagicMock(return_value=MagicMock(connect_ex=MagicMock(return_value=0)))
            mock_sock.return_value.__exit__ = MagicMock(return_value=False)
            self.assertFalse(_check_port(5050))


class TestVersionFlag(unittest.TestCase):

    def test_version_prints_and_returns(self):
        import io
        from contextlib import redirect_stdout
        with patch("sys.argv", ["finn-tracker", "--version"]):
            import importlib.metadata as _im
            with patch.object(_im, "version", return_value="0.1.0"):
                f = io.StringIO()
                with redirect_stdout(f):
                    from finn_tracker.__main__ import main
                    main()
                self.assertIn("0.1.0", f.getvalue())

    def test_version_dev_fallback(self):
        import io
        from contextlib import redirect_stdout
        import importlib.metadata as _im
        with patch("sys.argv", ["finn-tracker", "--version"]):
            with patch.object(_im, "version", side_effect=_im.PackageNotFoundError):
                f = io.StringIO()
                with redirect_stdout(f):
                    from finn_tracker.__main__ import main
                    main()
                self.assertIn("dev", f.getvalue())


if __name__ == "__main__":
    unittest.main()
