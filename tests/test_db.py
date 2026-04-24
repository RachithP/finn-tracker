"""
Tests for utils/db.py — shared data access layer.
Covers: DB connection, transaction ID generation, category overrides,
learned rules, custom categories, user transactions, folder scanning,
deduplication, pattern extraction, full pipeline, period filtering,
and analytics helpers.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from finn_tracker.utils.db import (
    _db_conn,
    make_txn_id,
    load_category_overrides,
    save_category_override,
    load_learned_rules,
    get_categories,
    _load_user_transactions,
    scan_folders,
    dedup,
    _extract_pattern,
    get_all_transactions,
    filter_by_period,
    _is_expense,
    get_spending_summary,
    get_top_merchants,
    get_monthly_trend,
)
from finn_tracker.models import DEFAULT_CATEGORIES


def _init_test_db(db_path: Path):
    """Create the schema in a fresh test DB."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS category_overrides (
            txn_id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS learned_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL,
            hit_count INTEGER DEFAULT 1,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS custom_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id TEXT UNIQUE,
            source_file TEXT,
            txn_json TEXT NOT NULL
        );
    """)
    conn.close()


class TestDBConnection(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _init_test_db(self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_connection_with_row_factory(self):
        conn = _db_conn(self.db_path)
        self.assertEqual(conn.row_factory, sqlite3.Row)
        conn.close()

    def test_wal_mode_enabled(self):
        conn = _db_conn(self.db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode, "wal")
        conn.close()


class TestMakeTxnId(unittest.TestCase):

    def test_deterministic(self):
        t = {"date": "2024-01-15", "merchant": "WHOLE FOODS", "amount": -89.47, "account": "Chase"}
        self.assertEqual(make_txn_id(t), make_txn_id(t))

    def test_length_is_12(self):
        t = {"date": "2024-01-15", "merchant": "X", "amount": -1.0, "account": "A"}
        self.assertEqual(len(make_txn_id(t)), 12)

    def test_different_inputs_different_ids(self):
        t1 = {"date": "2024-01-15", "merchant": "A", "amount": -10.0, "account": "X"}
        t2 = {"date": "2024-01-15", "merchant": "B", "amount": -10.0, "account": "X"}
        self.assertNotEqual(make_txn_id(t1), make_txn_id(t2))

    def test_missing_keys_still_works(self):
        t = {}
        tid = make_txn_id(t)
        self.assertEqual(len(tid), 12)


class TestLoadCategoryOverrides(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _init_test_db(self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_db_returns_empty_dict(self):
        result = load_category_overrides(self.db_path)
        self.assertEqual(result, {})

    def test_returns_saved_overrides(self):
        save_category_override("txn001", "Groceries", self.db_path)
        save_category_override("txn002", "Travel", self.db_path)
        result = load_category_overrides(self.db_path)
        self.assertEqual(result["txn001"], "Groceries")
        self.assertEqual(result["txn002"], "Travel")

    def test_upsert_updates_category(self):
        save_category_override("txn001", "Shopping", self.db_path)
        save_category_override("txn001", "Groceries", self.db_path)
        result = load_category_overrides(self.db_path)
        self.assertEqual(result["txn001"], "Groceries")

    def test_nonexistent_db_returns_empty_dict(self):
        result = load_category_overrides(Path("/nonexistent/path/db.sqlite"))
        self.assertEqual(result, {})


class TestLoadLearnedRules(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _init_test_db(self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_db_returns_empty_list(self):
        self.assertEqual(load_learned_rules(self.db_path), [])

    def test_returns_saved_rules(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO learned_rules (pattern, category) VALUES (?, ?)", ("AMAZON", "Shopping"))
        conn.commit()
        conn.close()
        rules = load_learned_rules(self.db_path)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["pattern"], "AMAZON")
        self.assertEqual(rules[0]["category"], "Shopping")

    def test_nonexistent_db_returns_empty_list(self):
        result = load_learned_rules(Path("/nonexistent/path/db.sqlite"))
        self.assertEqual(result, [])


class TestGetCategories(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _init_test_db(self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_defaults_when_no_custom(self):
        cats = get_categories(self.db_path)
        for c in DEFAULT_CATEGORIES:
            self.assertIn(c, cats)

    def test_includes_custom_categories(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO custom_categories (name) VALUES (?)", ("Pets",))
        conn.commit()
        conn.close()
        cats = get_categories(self.db_path)
        self.assertIn("Pets", cats)

    def test_no_duplicate_if_custom_matches_default(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO custom_categories (name) VALUES (?)", ("Groceries",))
        conn.commit()
        conn.close()
        cats = get_categories(self.db_path)
        self.assertEqual(cats.count("Groceries"), 1)

    def test_nonexistent_db_returns_defaults(self):
        cats = get_categories(Path("/nonexistent/path/db.sqlite"))
        self.assertEqual(cats, list(DEFAULT_CATEGORIES))


class TestLoadUserTransactions(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _init_test_db(self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_db_returns_empty_list(self):
        result = _load_user_transactions(self.db_path, {})
        self.assertEqual(result, [])

    def test_loads_saved_transactions(self):
        txn = {"date": "2024-01-15", "merchant": "STORE", "amount": -50.0, "account": "Test"}
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO user_transactions (txn_id, source_file, txn_json) VALUES (?, ?, ?)",
            ("tx001", "test.csv", json.dumps(txn))
        )
        conn.commit()
        conn.close()
        result = _load_user_transactions(self.db_path, {})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["merchant"], "STORE")

    def test_applies_category_overrides(self):
        txn = {"date": "2024-01-15", "merchant": "STORE", "amount": -50.0, "category": "Uncategorized"}
        tid = make_txn_id(txn)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO user_transactions (txn_id, source_file, txn_json) VALUES (?, ?, ?)",
            (tid, "test.csv", json.dumps(txn))
        )
        conn.commit()
        conn.close()
        overrides = {tid: "Groceries"}
        result = _load_user_transactions(self.db_path, overrides)
        self.assertEqual(result[0]["category"], "Groceries")

    def test_nonexistent_db_returns_empty_list(self):
        result = _load_user_transactions(Path("/nonexistent/path/db.sqlite"), {})
        self.assertEqual(result, [])


class TestDedup(unittest.TestCase):

    def test_empty_list(self):
        self.assertEqual(dedup([]), [])

    def test_no_duplicates(self):
        txns = [
            {"date": "2024-01-01", "merchant": "A", "amount": -10.0},
            {"date": "2024-01-02", "merchant": "B", "amount": -20.0},
        ]
        self.assertEqual(len(dedup(txns)), 2)

    def test_exact_duplicate_removed(self):
        t = {"date": "2024-01-01", "merchant": "STORE", "amount": -50.0}
        self.assertEqual(len(dedup([t, t])), 1)

    def test_case_insensitive_merchant(self):
        t1 = {"date": "2024-01-01", "merchant": "whole foods", "amount": -89.47}
        t2 = {"date": "2024-01-01", "merchant": "WHOLE FOODS", "amount": -89.47}
        self.assertEqual(len(dedup([t1, t2])), 1)

    def test_different_amounts_not_deduped(self):
        t1 = {"date": "2024-01-01", "merchant": "STORE", "amount": -50.0}
        t2 = {"date": "2024-01-01", "merchant": "STORE", "amount": -51.0}
        self.assertEqual(len(dedup([t1, t2])), 2)

    def test_preserves_first_occurrence(self):
        t1 = {"date": "2024-01-01", "merchant": "STORE", "amount": -50.0, "extra": "first"}
        t2 = {"date": "2024-01-01", "merchant": "STORE", "amount": -50.0, "extra": "second"}
        result = dedup([t1, t2])
        self.assertEqual(result[0]["extra"], "first")


class TestExtractPattern(unittest.TestCase):

    def test_strips_sq_prefix(self):
        self.assertEqual(_extract_pattern("SQ *COFFEE SHOP"), "COFFEE SHOP")

    def test_strips_tst_prefix(self):
        self.assertEqual(_extract_pattern("TST*RESTAURANT NAME"), "RESTAURANT NAME")

    def test_strips_pp_prefix(self):
        result = _extract_pattern("PP *PAYPAL MERCHANT")
        self.assertNotIn("PP", result.split()[0] if result else "")

    def test_strips_dash_suffix(self):
        self.assertEqual(_extract_pattern("STORE NAME - CITY"), "STORE NAME")

    def test_strips_long_numbers(self):
        result = _extract_pattern("STORE 123456")
        self.assertNotIn("123456", result)

    def test_dots_replaced_with_spaces(self):
        result = _extract_pattern("NETFLIX.COM")
        self.assertIn("NETFLIX", result)
        self.assertNotIn(".", result)

    def test_max_two_words(self):
        result = _extract_pattern("VERY LONG MERCHANT NAME HERE")
        self.assertLessEqual(len(result.split()), 2)


class TestFilterByPeriod(unittest.TestCase):

    def setUp(self):
        today = date.today()
        self.txns = [
            {"date": today.isoformat(), "merchant": "TODAY", "amount": -10.0},
            {"date": (today - timedelta(days=15)).isoformat(), "merchant": "RECENT", "amount": -20.0},
            {"date": (today - timedelta(days=60)).isoformat(), "merchant": "TWO_MONTHS", "amount": -30.0},
            {"date": (today - timedelta(days=200)).isoformat(), "merchant": "OLD", "amount": -40.0},
            {"date": "2023-06-15", "merchant": "ANCIENT", "amount": -50.0},
        ]

    def test_all_returns_everything(self):
        result = filter_by_period(self.txns, "all")
        self.assertEqual(len(result), len(self.txns))

    def test_empty_period_returns_everything(self):
        result = filter_by_period(self.txns, "")
        self.assertEqual(len(result), len(self.txns))

    def test_1m_filters_recent(self):
        result = filter_by_period(self.txns, "1m")
        self.assertTrue(all(t["merchant"] in ("TODAY", "RECENT") for t in result))

    def test_thismonth_filters_current_month(self):
        result = filter_by_period(self.txns, "thismonth")
        prefix = date.today().strftime("%Y-%m")
        self.assertTrue(all(t["date"].startswith(prefix) for t in result))

    def test_lastmonth_filters_previous_month(self):
        today = date.today()
        if today.month == 1:
            last = today.replace(year=today.year - 1, month=12, day=1)
        else:
            last = today.replace(month=today.month - 1, day=1)
        prefix = last.strftime("%Y-%m")
        result = filter_by_period(self.txns, "lastmonth")
        self.assertTrue(all(t["date"].startswith(prefix) for t in result))

    def test_ytd_filters_current_year(self):
        result = filter_by_period(self.txns, "ytd")
        year_prefix = str(date.today().year)
        self.assertTrue(all(t["date"].startswith(year_prefix) for t in result))

    def test_custom_with_range(self):
        result = filter_by_period(self.txns, "custom", "2023-01-01", "2023-12-31")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["merchant"], "ANCIENT")

    def test_custom_without_dates_returns_all(self):
        result = filter_by_period(self.txns, "custom")
        self.assertEqual(len(result), len(self.txns))

    def test_unknown_period_returns_all(self):
        result = filter_by_period(self.txns, "unknown_period")
        self.assertEqual(len(result), len(self.txns))

    def test_3m_filter(self):
        result = filter_by_period(self.txns, "3m")
        # Should include today, recent, and two_months but not old or ancient
        merchants = {t["merchant"] for t in result}
        self.assertIn("TODAY", merchants)
        self.assertNotIn("ANCIENT", merchants)

    def test_6m_filter(self):
        result = filter_by_period(self.txns, "6m")
        merchants = {t["merchant"] for t in result}
        self.assertIn("TODAY", merchants)
        self.assertNotIn("ANCIENT", merchants)

    def test_txn_without_date_excluded(self):
        txns = [{"merchant": "NO_DATE", "amount": -10.0}]
        result = filter_by_period(txns, "1m")
        self.assertEqual(len(result), 0)


class TestIsExpense(unittest.TestCase):

    def test_negative_amount_is_expense(self):
        self.assertTrue(_is_expense({"amount": -50.0, "source_folder": "expense"}))

    def test_positive_amount_not_expense(self):
        self.assertFalse(_is_expense({"amount": 50.0, "source_folder": "expense"}))

    def test_income_folder_not_expense(self):
        self.assertFalse(_is_expense({"amount": -50.0, "source_folder": "income"}))

    def test_default_source_folder_is_expense(self):
        self.assertTrue(_is_expense({"amount": -50.0}))


class TestGetSpendingSummary(unittest.TestCase):

    def setUp(self):
        today = date.today()
        self.txns = [
            {"date": today.isoformat(), "merchant": "WHOLE FOODS", "amount": -89.47,
             "category": "Groceries", "source_folder": "expense"},
            {"date": today.isoformat(), "merchant": "SHELL OIL", "amount": -45.00,
             "category": "Gas & Fuel", "source_folder": "expense"},
            {"date": today.isoformat(), "merchant": "PAYROLL", "amount": 3200.0,
             "category": "Income", "source_folder": "income"},
        ]

    def test_total_is_sum_of_expenses(self):
        summary = get_spending_summary(self.txns, "all")
        self.assertAlmostEqual(summary["total"], 134.47)

    def test_count_excludes_income(self):
        summary = get_spending_summary(self.txns, "all")
        self.assertEqual(summary["count"], 2)

    def test_by_category_breakdown(self):
        summary = get_spending_summary(self.txns, "all")
        cats = {c["category"]: c for c in summary["by_category"]}
        self.assertIn("Groceries", cats)
        self.assertIn("Gas & Fuel", cats)
        self.assertAlmostEqual(cats["Groceries"]["total"], 89.47)

    def test_percentages_sum_to_100(self):
        summary = get_spending_summary(self.txns, "all")
        total_pct = sum(c["pct"] for c in summary["by_category"])
        self.assertAlmostEqual(total_pct, 100.0, places=0)

    def test_empty_txns_returns_zero(self):
        summary = get_spending_summary([], "all")
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["count"], 0)


class TestGetTopMerchants(unittest.TestCase):

    def setUp(self):
        today = date.today()
        self.txns = [
            {"date": today.isoformat(), "merchant": "WHOLE FOODS", "amount": -89.47, "source_folder": "expense"},
            {"date": today.isoformat(), "merchant": "WHOLE FOODS", "amount": -45.00, "source_folder": "expense"},
            {"date": today.isoformat(), "merchant": "SHELL OIL", "amount": -30.00, "source_folder": "expense"},
            {"date": today.isoformat(), "merchant": "PAYROLL", "amount": 3200.0, "source_folder": "income"},
        ]

    def test_returns_ranked_merchants(self):
        result = get_top_merchants(self.txns, "all", limit=10)
        self.assertEqual(result[0]["merchant"], "WHOLE FOODS")
        self.assertAlmostEqual(result[0]["total"], 134.47)
        self.assertEqual(result[0]["count"], 2)

    def test_limit_respected(self):
        result = get_top_merchants(self.txns, "all", limit=1)
        self.assertEqual(len(result), 1)

    def test_excludes_income(self):
        result = get_top_merchants(self.txns, "all", limit=10)
        merchants = [r["merchant"] for r in result]
        self.assertNotIn("PAYROLL", merchants)


class TestGetMonthlyTrend(unittest.TestCase):

    def test_returns_correct_number_of_months(self):
        result = get_monthly_trend([], months=6)
        self.assertEqual(len(result), 6)

    def test_months_sorted_oldest_first(self):
        result = get_monthly_trend([], months=3)
        months = [r["month"] for r in result]
        self.assertEqual(months, sorted(months))

    def test_expenses_and_income_calculated(self):
        today = date.today()
        key = today.strftime("%Y-%m")
        txns = [
            {"date": today.isoformat(), "merchant": "STORE", "amount": -100.0, "source_folder": "expense"},
            {"date": today.isoformat(), "merchant": "PAYROLL", "amount": 3000.0, "source_folder": "income"},
        ]
        result = get_monthly_trend(txns, months=1)
        current = result[0]
        self.assertEqual(current["month"], key)
        self.assertAlmostEqual(current["expenses"], 100.0)
        self.assertAlmostEqual(current["income"], 3000.0)
        self.assertAlmostEqual(current["net"], 2900.0)

    def test_txn_outside_range_ignored(self):
        txns = [{"date": "2020-01-15", "merchant": "OLD", "amount": -50.0, "source_folder": "expense"}]
        result = get_monthly_trend(txns, months=3)
        total_expenses = sum(r["expenses"] for r in result)
        self.assertAlmostEqual(total_expenses, 0.0)


class TestScanFolders(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.expense_dir = Path(self.tmpdir) / "expense"
        self.income_dir = Path(self.tmpdir) / "income"
        self.expense_dir.mkdir()
        self.income_dir.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_folders_returns_empty(self):
        result = scan_folders({}, [self.expense_dir, self.income_dir])
        self.assertEqual(result, [])

    def test_missing_folder_skipped(self):
        result = scan_folders({}, [Path("/nonexistent/folder")])
        self.assertEqual(result, [])

    def test_scans_csv_files(self):
        csv_content = "Date,Description,Amount\n2024-01-01,Test Store,-25.00\n"
        (self.expense_dir / "test.csv").write_text(csv_content)
        result = scan_folders({}, [self.expense_dir])
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0]["source_folder"], "expense")

    def test_applies_overrides(self):
        csv_content = "Date,Description,Amount\n2024-01-01,Test Store,-25.00\n"
        (self.expense_dir / "test.csv").write_text(csv_content)
        result = scan_folders({}, [self.expense_dir])
        if result:
            tid = result[0]["txn_id"]
            result_with_override = scan_folders({tid: "Groceries"}, [self.expense_dir])
            self.assertEqual(result_with_override[0]["category"], "Groceries")

    def test_ignores_non_csv_pdf_files(self):
        (self.expense_dir / "notes.txt").write_text("hello")
        result = scan_folders({}, [self.expense_dir])
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
