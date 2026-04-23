"""
Combined tests for Expense Tracker.
Runs parser unit tests, ingest engine tests, Flask endpoint tests, export tests,
folder scanner tests, privacy masking tests, and duplicate detection tests.
Run with: python -m pytest tests/ -v
"""
import csv
import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import pandas as pd

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import app as flask_app
from models import Transaction, ParseResult, DEFAULT_CATEGORIES, mask_sensitive

# ── Test DB isolation ─────────────────────────────────────────────────────────
# Redirect the app's DB to a dedicated test file for the entire test run so
# tests never read from or write to the live data/finn_tracker.db.
# The file is created in setUpModule and deleted in tearDownModule.

_TEST_DB_PATH = Path(__file__).parent / "test_finn_tracker.db"
_ORIG_DB_PATH = flask_app.DB_PATH


def setUpModule():
    flask_app.DB_PATH = _TEST_DB_PATH
    flask_app._init_db()
    flask_app._load_session_from_db()


def tearDownModule():
    flask_app.DB_PATH = _ORIG_DB_PATH
    try:
        _TEST_DB_PATH.unlink(missing_ok=True)
    except OSError:
        pass
from parsers.csv_parser import parse_csv, _parse_amount, _parse_date, _detect_format
from parsers.pdf_parser import _extract_date_from_cell, _parse_amount as pdf_parse_amount, _parse_date as pdf_parse_date
from ingest import ingest_file, merge_results
from sample_data.generators import (
    write_sample_files, make_bad_csv,
    CHASE_BANK_CSV, CHASE_CREDIT_CSV, BOFA_BANK_CSV, GENERIC_CSV
)


# ══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestTransaction(unittest.TestCase):

    def test_basic_creation(self):
        t = Transaction(date=date(2024, 1, 15), merchant="Coffee Shop", amount=5.50)
        self.assertEqual(t.date, date(2024, 1, 15))
        self.assertEqual(t.merchant, "Coffee Shop")
        self.assertAlmostEqual(t.amount, 5.50)
        self.assertEqual(t.category, "Uncategorized")

    def test_to_dict_returns_correct_keys(self):
        t = Transaction(date=date(2024, 1, 1), merchant="Store", amount=10.00)
        d = t.to_dict()
        self.assertIn("date", d)
        self.assertIn("merchant", d)
        self.assertIn("amount", d)
        self.assertIn("category", d)
        self.assertNotIn("raw_description", d)

    def test_to_dict_date_is_string(self):
        t = Transaction(date=date(2024, 3, 5), merchant="X", amount=1.0)
        self.assertEqual(t.to_dict()["date"], "2024-03-05")

    def test_amount_rounded_in_dict(self):
        t = Transaction(date=date(2024, 1, 1), merchant="X", amount=10.999)
        self.assertEqual(t.to_dict()["amount"], 11.0)

    def test_repr_does_not_expose_raw_account(self):
        t = Transaction(date=date(2024, 1, 1), merchant="Store", amount=10.0,
                        account="Chase ••4231")
        r = repr(t)
        self.assertIn("Store", r)
        self.assertNotIn("raw", r.lower())


class TestMaskSensitive(unittest.TestCase):

    def test_masks_card_number(self):
        masked = mask_sensitive("Card: 4111 1111 1111 1111 charged")
        self.assertNotIn("4111 1111 1111 1111", masked)

    def test_masks_card_number_nodash(self):
        masked = mask_sensitive("4111111111111111")
        self.assertNotIn("4111111111111111", masked)

    def test_masks_ssn(self):
        masked = mask_sensitive("SSN: 123-45-6789")
        self.assertNotIn("123-45-6789", masked)

    def test_masks_long_account_number(self):
        masked = mask_sensitive("Account: 123456789012")
        self.assertNotIn("123456789012", masked)

    def test_normal_text_unchanged(self):
        text = "WHOLE FOODS MARKET #123"
        self.assertEqual(mask_sensitive(text), text)

    def test_empty_string(self):
        self.assertEqual(mask_sensitive(""), "")

    def test_none_passthrough(self):
        self.assertEqual(mask_sensitive(None), None)


class TestParseResult(unittest.TestCase):

    def test_empty_result_not_success(self):
        r = ParseResult()
        self.assertFalse(r.success)

    def test_result_with_transactions_is_success(self):
        r = ParseResult()
        r.transactions = [Transaction(date=date(2024, 1, 1), merchant="X", amount=5.0)]
        self.assertTrue(r.success)

    def test_summary_totals(self):
        r = ParseResult()
        r.transactions = [
            Transaction(date=date(2024, 1, 1), merchant="A", amount=-100.0),  # charge (expense)
            Transaction(date=date(2024, 1, 2), merchant="B", amount=50.0),    # payment (income)
        ]
        s = r.summary
        self.assertEqual(s["total_expenses"], 100.0)
        self.assertEqual(s["total_income"], 50.0)
        self.assertEqual(s["net"], -50.0)
        self.assertEqual(s["count"], 2)

    def test_default_categories_present(self):
        self.assertIn("Food & Dining", DEFAULT_CATEGORIES)
        self.assertIn("Uncategorized", DEFAULT_CATEGORIES)
        self.assertGreater(len(DEFAULT_CATEGORIES), 10)


# ══════════════════════════════════════════════════════════════════════════════
# CSV PARSER UNIT TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestParseAmount(unittest.TestCase):

    def test_simple_negative(self):
        self.assertAlmostEqual(_parse_amount("-89.47"), -89.47)

    def test_positive(self):
        self.assertAlmostEqual(_parse_amount("3200.00"), 3200.0)

    def test_with_dollar_sign(self):
        self.assertAlmostEqual(_parse_amount("$52.10"), 52.10)

    def test_with_comma(self):
        self.assertAlmostEqual(_parse_amount("1,234.56"), 1234.56)

    def test_parenthesis_negative(self):
        self.assertAlmostEqual(_parse_amount("(100.00)"), -100.0)

    def test_invalid_returns_none(self):
        self.assertIsNone(_parse_amount("not_a_number"))

    def test_empty_returns_none(self):
        self.assertIsNone(_parse_amount(""))


class TestParseDate(unittest.TestCase):

    def test_mmddyyyy(self):
        self.assertEqual(_parse_date("01/15/2024"), date(2024, 1, 15))

    def test_yyyymmdd(self):
        self.assertEqual(_parse_date("2024-01-15"), date(2024, 1, 15))

    def test_mmddyy(self):
        self.assertEqual(_parse_date("01/15/24"), date(2024, 1, 15))

    def test_with_explicit_fmt(self):
        self.assertEqual(_parse_date("01/15/2024", "%m/%d/%Y"), date(2024, 1, 15))

    def test_invalid_returns_none(self):
        self.assertIsNone(_parse_date("NOT_A_DATE"))


class TestFormatDetection(unittest.TestCase):

    def test_detects_chase_bank(self):
        cols = {"Details", "Posting Date", "Description", "Amount", "Type", "Balance"}
        self.assertEqual(_detect_format(cols), "chase_bank")

    def test_detects_chase_credit(self):
        cols = {"Transaction Date", "Post Date", "Description", "Category",
                "Type", "Amount", "Memo"}
        self.assertEqual(_detect_format(cols), "chase_credit")

    def test_detects_bofa_bank(self):
        cols = {"Date", "Description", "Amount", "Running Bal."}
        self.assertEqual(_detect_format(cols), "bofa_bank")

    def test_detects_bofa_credit(self):
        cols = {"Posted Date", "Reference Number", "Payee", "Address", "Amount"}
        self.assertEqual(_detect_format(cols), "bofa_credit")

    def test_unknown_falls_back_to_generic(self):
        cols = {"date", "merchant", "amount", "notes"}
        self.assertEqual(_detect_format(cols), "generic")


class TestCSVParser(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.files = write_sample_files(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _get_file(self, name):
        return str(Path(self.tmp) / name)

    def test_chase_bank_parses_all_rows(self):
        result = parse_csv(self._get_file("chase_bank_sample.csv"), "Chase ••1234")
        self.assertTrue(result.success)
        self.assertEqual(len(result.transactions), 10)
        self.assertEqual(result.parser_used, "csv_chase_bank")

    def test_chase_bank_amounts_correct(self):
        result = parse_csv(self._get_file("chase_bank_sample.csv"))
        expenses = [t for t in result.transactions if t.amount < 0]
        income = [t for t in result.transactions if t.amount > 0]
        self.assertGreater(len(expenses), 0)
        self.assertGreater(len(income), 0)

    def test_chase_credit_parses_all_rows(self):
        result = parse_csv(self._get_file("chase_credit_sample.csv"))
        self.assertTrue(result.success)
        self.assertEqual(len(result.transactions), 10)
        self.assertEqual(result.parser_used, "csv_chase_credit")

    def test_chase_credit_charge_is_negative(self):
        """Chase credit CSV charges are negative in source; parser preserves that sign."""
        result = parse_csv(self._get_file("chase_credit_sample.csv"))
        uber = next((t for t in result.transactions if "UBER" in t.merchant), None)
        self.assertIsNotNone(uber)
        self.assertLess(uber.amount, 0)

    def test_capital_one_debit_credit_signs(self):
        """Capital One CSVs with Debit/Credit columns should produce signed expense amounts."""
        csv_content = """Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
2024-03-01,2024-03-02,1234,RESTAURANT,Food & Dining,45.00,
2024-03-05,2024-03-06,1234,ONLINE STORE,Shopping,,2500.00
"""
        path = Path(self.tmp) / "capital_one_sample.csv"
        path.write_text(csv_content)

        result = parse_csv(str(path))
        self.assertTrue(result.success)
        self.assertEqual(len(result.transactions), 2)
        self.assertEqual(result.parser_used, "csv_capital_one")

        debit_txn = next((t for t in result.transactions if "RESTAURANT" in t.merchant), None)
        credit_txn = next((t for t in result.transactions if "ONLINE STORE" in t.merchant), None)
        self.assertIsNotNone(debit_txn)
        self.assertIsNotNone(credit_txn)
        self.assertAlmostEqual(debit_txn.amount, -45.00)
        self.assertAlmostEqual(credit_txn.amount, 2500.00)

    def test_bofa_bank_parses(self):
        result = parse_csv(self._get_file("bofa_bank_sample.csv"))
        self.assertTrue(result.success)
        self.assertEqual(len(result.transactions), 10)

    def test_generic_csv_parses(self):
        result = parse_csv(self._get_file("generic_sample.csv"))
        self.assertTrue(result.success)
        self.assertGreater(len(result.transactions), 0)

    def test_all_transactions_have_dates(self):
        result = parse_csv(self._get_file("chase_bank_sample.csv"))
        for t in result.transactions:
            self.assertIsInstance(t.date, date)

    def test_all_transactions_have_amounts(self):
        result = parse_csv(self._get_file("chase_bank_sample.csv"))
        for t in result.transactions:
            self.assertIsInstance(t.amount, float)

    def test_account_label_stored(self):
        result = parse_csv(self._get_file("chase_bank_sample.csv"), "Chase ••9999")
        for t in result.transactions:
            self.assertEqual(t.account, "Chase ••9999")

    def test_source_file_set(self):
        result = parse_csv(self._get_file("chase_bank_sample.csv"))
        for t in result.transactions:
            self.assertEqual(t.source_file, "chase_bank_sample.csv")

    def test_category_defaults_to_uncategorized(self):
        result = parse_csv(self._get_file("chase_bank_sample.csv"))
        for t in result.transactions:
            self.assertEqual(t.category, "Uncategorized")

    def test_malformed_rows_produce_errors_not_crash(self):
        bad_file = make_bad_csv(self.tmp)
        result = parse_csv(bad_file)
        self.assertGreater(len(result.errors), 0)
        self.assertEqual(len(result.transactions), 1)

    def test_nonexistent_file_error(self):
        result = ingest_file("/nonexistent/path/file.csv")
        self.assertFalse(result.success)
        self.assertGreater(len(result.errors), 0)

    def test_unsupported_extension(self):
        p = Path(self.tmp) / "something.xlsx"
        p.write_text("fake content")
        result = ingest_file(str(p))
        self.assertFalse(result.success)
        self.assertIn("Unsupported", result.errors[0])

    def test_no_pii_in_to_dict(self):
        result = parse_csv(self._get_file("chase_bank_sample.csv"))
        for t in result.transactions:
            d = t.to_dict()
            for v in d.values():
                v_str = str(v)
                self.assertIsNone(re.search(r'\b\d{16}\b', v_str))


# ══════════════════════════════════════════════════════════════════════════════
# PDF PARSER UNIT TESTS
# ══════════════════════════════════════════════════════════════════════

class TestPDFParserHelpers(unittest.TestCase):

    def test_parse_date_slash(self):
        self.assertEqual(pdf_parse_date("01/15/2024"), date(2024, 1, 15))

    def test_parse_date_iso(self):
        self.assertEqual(pdf_parse_date("2024-01-15"), date(2024, 1, 15))

    def test_parse_date_month_name(self):
        self.assertEqual(pdf_parse_date("Jan 15, 2024"), date(2024, 1, 15))

    def test_parse_date_invalid(self):
        self.assertIsNone(pdf_parse_date("NOTADATE"))

    def test_parse_amount_dollar(self):
        self.assertAlmostEqual(pdf_parse_amount("$45.67"), 45.67)

    def test_parse_amount_negative(self):
        self.assertAlmostEqual(pdf_parse_amount("-$100.00"), -100.00)

    def test_parse_amount_paren(self):
        self.assertAlmostEqual(pdf_parse_amount("(50.00)"), -50.00)

    def test_extract_date_from_cell(self):
        self.assertEqual(_extract_date_from_cell("01/15/2024"), date(2024, 1, 15))
        self.assertEqual(_extract_date_from_cell("Purchase on 2024-03-10"), date(2024, 3, 10))
        self.assertIsNone(_extract_date_from_cell("No date here"))


# ══════════════════════════════════════════════════════════════════════════════
# INGEST ENGINE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestIngestEngine(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.files = write_sample_files(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ingest_single_csv(self):
        result = ingest_file(self.files[0])
        self.assertTrue(result.success)

    def test_merge_results_sorted_descending(self):
        results = [ingest_file(f) for f in self.files]
        merged = merge_results(results)
        dates = [t.date for t in merged]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_merge_results_combines_all(self):
        results = [ingest_file(f) for f in self.files]
        merged = merge_results(results)
        total_expected = sum(len(r.transactions) for r in results)
        self.assertEqual(len(merged), total_expected)

    def test_summary_net_calculation(self):
        result = ingest_file(self.files[0])
        s = result.summary
        computed = round(sum(t.amount for t in result.transactions), 2)
        self.assertAlmostEqual(s["net"], computed, places=1)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 FLASK + EXPORT TESTS
# ══════════════════════════════════════════════════════════════════════════════

SAMPLE_TXN = [
    {"date": "2024-01-03", "merchant": "WHOLE FOODS", "amount": -89.47, "category": "Groceries",   "account": "Chase Bank",   "source_file": "test.csv"},
    {"date": "2024-01-05", "merchant": "NETFLIX.COM",  "amount": -15.99, "category": "Subscriptions","account": "Chase Bank",   "source_file": "test.csv"},
    {"date": "2024-01-08", "merchant": "DELTA AIR",    "amount": -420.0, "category": "Travel",       "account": "Chase Credit", "source_file": "test.csv"},
    {"date": "2024-01-10", "merchant": "PAYROLL",      "amount": 3200.0, "category": "Income",       "account": "Chase Bank",   "source_file": "test.csv"},
]


class TestFlaskEndpoints(unittest.TestCase):

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        flask_app._session["categories"].clear()

    def test_root_returns_200_html(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"text/html", r.content_type.encode())
        self.assertIn(b"finn-tracker", r.data)

    def test_import_files_no_files_returns_400(self):
        r = self.client.post("/import/files")
        self.assertEqual(r.status_code, 400)
        data = json.loads(r.data)
        self.assertIn("error", data)

    def test_import_folder_missing_param_returns_400(self):
        r = self.client.post("/import/folder",
                             data=json.dumps({}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_import_folder_nonexistent_returns_404(self):
        r = self.client.post("/import/folder",
                             data=json.dumps({"folder": "/nonexistent/path/xyz"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 404)

    def test_check_duplicates_returns_json(self):
        payload = {"new_transactions": SAMPLE_TXN[:1], "existing_transactions": []}
        r = self.client.post("/check-duplicates",
                             data=json.dumps(payload),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.data)
        self.assertIn("duplicates", data)
        self.assertIn("count", data)

    def test_export_csv_missing_path_returns_400(self):
        payload = {"transactions": SAMPLE_TXN, "save_path": ""}
        r = self.client.post("/export/csv",
                             data=json.dumps(payload),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_export_pdf_missing_path_returns_400(self):
        payload = {"transactions": SAMPLE_TXN, "save_path": ""}
        r = self.client.post("/export/pdf",
                             data=json.dumps(payload),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_export_csv_no_transactions_returns_400(self):
        payload = {"transactions": [], "save_path": "/tmp/empty.csv"}
        r = self.client.post("/export/csv",
                             data=json.dumps(payload),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_categories_update_valid(self):
        txn_id = flask_app._make_txn_id(SAMPLE_TXN[0])
        payload = {"txn_id": txn_id, "category": "Travel"}
        r = self.client.post("/categories/update",
                             data=json.dumps(payload),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["category"], "Travel")

    def test_categories_update_invalid_category(self):
        txn_id = flask_app._make_txn_id(SAMPLE_TXN[0])
        payload = {"txn_id": txn_id, "category": "NotARealCategory"}
        r = self.client.post("/categories/update",
                             data=json.dumps(payload),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_categories_update_missing_txn_id(self):
        r = self.client.post("/categories/update",
                             data=json.dumps({"category": "Travel"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_chat_config_endpoint(self):
        """Test /chat/config returns expected configuration."""
        r = self.client.get("/chat/config")
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.data)
        self.assertIn("maxTrendMonths", data)
        self.assertEqual(data["maxTrendMonths"], 36)

    def test_chat_endpoint_accepts_frontend_context(self):
        """Test /chat endpoint accepts frontend-provided data structure."""
        payload = {
            "message": "How much did I spend?",
            "history": [],
            "data": {
                "monthlyTrend": [
                    {"month": "2026-04", "expenses": 350.00, "income": 3000.00, "net": 2650.00,
                     "categories": [{"category": "Shopping", "total": 200.00}],
                     "merchants": [{"merchant": "AMAZON", "total": 150.50}]}
                ],
                "categories": ["Shopping", "Food & Dining"]
            }
        }
        r = self.client.post("/chat",
                             data=json.dumps(payload),
                             content_type="application/json")
        # Should return 200 even if llama-server isn't running (will fail gracefully)
        self.assertIn(r.status_code, [200, 500])

    def test_chat_endpoint_missing_message(self):
        """Test /chat endpoint returns 400 when message is missing."""
        payload = {
            "history": [],
            "data": {"topMerchants": [], "categoryTotals": [], "monthlyTrend": [], "recentTransactions": [], "categories": []}
        }
        r = self.client.post("/chat",
                             data=json.dumps(payload),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        data = json.loads(r.data)
        self.assertIn("error", data)


class TestDuplicateDetection(unittest.TestCase):

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def _check(self, new_txns, existing):
        r = self.client.post("/check-duplicates",
                             data=json.dumps({"new_transactions": new_txns, "existing_transactions": existing}),
                             content_type="application/json")
        return json.loads(r.data)

    def test_exact_duplicate_detected(self):
        t = {"date": "2024-01-03", "merchant": "WHOLE FOODS", "amount": -89.47, "account": "Chase Bank"}
        result = self._check([t], [t])
        self.assertEqual(result["count"], 1)

    def test_near_amount_duplicate_detected(self):
        t1 = {"date": "2024-01-03", "merchant": "WHOLE FOODS", "amount": -89.47, "account": "Chase Bank"}
        t2 = {"date": "2024-01-03", "merchant": "whole foods", "amount": -89.47, "account": "Chase Bank"}
        result = self._check([t1], [t2])
        self.assertEqual(result["count"], 1)

    def test_different_date_not_duplicate(self):
        t1 = {"date": "2024-01-03", "merchant": "WHOLE FOODS", "amount": -89.47, "account": "Chase Bank"}
        t2 = {"date": "2024-01-10", "merchant": "WHOLE FOODS", "amount": -89.47, "account": "Chase Bank"}
        result = self._check([t1], [t2])
        self.assertEqual(result["count"], 0)

    def test_different_amount_not_duplicate(self):
        t1 = {"date": "2024-01-03", "merchant": "WHOLE FOODS", "amount": -89.47, "account": "Chase Bank"}
        t2 = {"date": "2024-01-03", "merchant": "WHOLE FOODS", "amount": -90.00, "account": "Chase Bank"}
        result = self._check([t1], [t2])
        self.assertEqual(result["count"], 0)

    def test_different_merchant_not_duplicate(self):
        t1 = {"date": "2024-01-03", "merchant": "WHOLE FOODS",   "amount": -89.47, "account": "Chase Bank"}
        t2 = {"date": "2024-01-03", "merchant": "TRADER JOE'S",  "amount": -89.47, "account": "Chase Bank"}
        result = self._check([t1], [t2])
        self.assertEqual(result["count"], 0)

    def test_case_insensitive_merchant_match(self):
        t1 = {"date": "2024-01-05", "merchant": "netflix.com", "amount": -15.99, "account": "Chase Bank"}
        t2 = {"date": "2024-01-05", "merchant": "NETFLIX.COM", "amount": -15.99, "account": "Chase Bank"}
        result = self._check([t1], [t2])
        self.assertEqual(result["count"], 1)

    def test_empty_new_batch_returns_zero(self):
        result = self._check([], SAMPLE_TXN)
        self.assertEqual(result["count"], 0)

    def test_amount_within_penny_is_duplicate(self):
        t1 = {"date": "2024-01-03", "merchant": "WHOLE FOODS", "amount": -89.47,  "account": "Chase Bank"}
        t2 = {"date": "2024-01-03", "merchant": "WHOLE FOODS", "amount": -89.475, "account": "Chase Bank"}
        result = self._check([t1], [t2])
        self.assertEqual(result["count"], 1)


class TestCSVExport(unittest.TestCase):

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_csv_export_creates_file(self):
        out = os.path.join(self.tmpdir, "out.csv")
        r = self.client.post("/export/csv",
                             data=json.dumps({"transactions": SAMPLE_TXN, "save_path": out}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(os.path.exists(out))

    def test_csv_export_correct_row_count(self):
        out = os.path.join(self.tmpdir, "out.csv")
        self.client.post("/export/csv",
                         data=json.dumps({"transactions": SAMPLE_TXN, "save_path": out}),
                         content_type="application/json")
        with open(out, newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), len(SAMPLE_TXN))

    def test_csv_export_correct_headers(self):
        out = os.path.join(self.tmpdir, "headers.csv")
        self.client.post("/export/csv",
                         data=json.dumps({"transactions": SAMPLE_TXN, "save_path": out}),
                         content_type="application/json")
        with open(out, newline="") as f:
            headers = next(csv.reader(f))
        for col in ["date", "merchant", "amount", "category", "account"]:
            self.assertIn(col, headers)

    def test_csv_export_returns_saved_path(self):
        out = os.path.join(self.tmpdir, "path_check.csv")
        r = self.client.post("/export/csv",
                             data=json.dumps({"transactions": SAMPLE_TXN, "save_path": out}),
                             content_type="application/json")
        data = json.loads(r.data)
        self.assertEqual(data["saved_path"], out)
        self.assertEqual(data["rows"], len(SAMPLE_TXN))

    def test_csv_export_creates_parent_dirs(self):
        nested = os.path.join(self.tmpdir, "sub", "deep", "out.csv")
        r = self.client.post("/export/csv",
                             data=json.dumps({"transactions": SAMPLE_TXN, "save_path": nested}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(os.path.exists(nested))


class TestPDFExport(unittest.TestCase):

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pdf_export_creates_file(self):
        out = os.path.join(self.tmpdir, "report.pdf")
        r = self.client.post("/export/pdf",
                             data=json.dumps({"transactions": SAMPLE_TXN, "save_path": out}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(os.path.exists(out))

    def test_pdf_export_file_nonempty(self):
        out = os.path.join(self.tmpdir, "report.pdf")
        self.client.post("/export/pdf",
                         data=json.dumps({"transactions": SAMPLE_TXN, "save_path": out}),
                         content_type="application/json")
        self.assertGreater(os.path.getsize(out), 0)

    def test_pdf_export_starts_with_pdf_magic(self):
        out = os.path.join(self.tmpdir, "magic.pdf")
        self.client.post("/export/pdf",
                         data=json.dumps({"transactions": SAMPLE_TXN, "save_path": out}),
                         content_type="application/json")
        with open(out, "rb") as f:
            header = f.read(4)
        self.assertEqual(header, b"%PDF")

    def test_pdf_export_returns_saved_path(self):
        out = os.path.join(self.tmpdir, "path.pdf")
        r = self.client.post("/export/pdf",
                             data=json.dumps({"transactions": SAMPLE_TXN, "save_path": out}),
                             content_type="application/json")
        data = json.loads(r.data)
        self.assertEqual(data["saved_path"], out)


class TestFolderScanner(unittest.TestCase):

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _scan(self, folder):
        r = self.client.post("/import/folder",
                             data=json.dumps({"folder": folder}),
                             content_type="application/json")
        return r, json.loads(r.data)

    def _write(self, name, content="Date,Description,Amount\n2024-01-01,Test,-10.00\n"):
        p = Path(self.tmpdir) / name
        p.write_text(content)
        return str(p)

    def test_finds_csv_files(self):
        self._write("bank.csv")
        r, data = self._scan(self.tmpdir)
        self.assertEqual(r.status_code, 200)
        self.assertIn("bank.csv", data["files_scanned"])

    def test_ignores_non_csv_pdf_files(self):
        self._write("notes.txt", "hello")
        self._write("data.xlsx", "nope")
        self._write("valid.csv")
        r, data = self._scan(self.tmpdir)
        self.assertEqual(r.status_code, 200)
        scanned = data["files_scanned"]
        self.assertNotIn("notes.txt", scanned)
        self.assertNotIn("data.xlsx", scanned)
        self.assertIn("valid.csv", scanned)

    def test_empty_folder_returns_200_with_warning(self):
        r, data = self._scan(self.tmpdir)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(data["count"], 0)
        self.assertTrue(any("No CSV or PDF" in e for e in data["errors"]))

    def test_nonexistent_folder_returns_404(self):
        r, data = self._scan("/does/not/exist/xyz")
        self.assertEqual(r.status_code, 404)

    def test_multiple_csv_files_all_scanned(self):
        self._write("a.csv")
        self._write("b.csv")
        self._write("c.csv")
        r, data = self._scan(self.tmpdir)
        self.assertEqual(len(data["files_scanned"]), 3)

    def test_folder_path_with_tilde_expanded(self):
        home = str(Path.home())
        r, data = self._scan("~")
        self.assertNotEqual(r.status_code, 404)


class TestPrivacyMasking(unittest.TestCase):

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _import_csv(self, content):
        path = os.path.join(self.tmpdir, "pii_test.csv")
        Path(path).write_text(content)
        with open(path, "rb") as f:
            r = self.client.post("/import/files",
                                 data={"files": (f, "pii_test.csv")},
                                 content_type="multipart/form-data")
        return json.loads(r.data)

    def test_card_number_masked_in_response(self):
        csv_content = "date,merchant,amount,notes\n2024-01-01,PURCHASE 4111111111111111,-50.00,\n"
        data = self._import_csv(csv_content)
        merchants = [t["merchant"] for t in data.get("transactions", [])]
        for m in merchants:
            self.assertNotIn("4111111111111111", m, f"Card number exposed in: {m}")

    def test_ssn_masked_in_response(self):
        csv_content = "date,merchant,amount,notes\n2024-01-01,REF 123-45-6789 PAYMENT,-25.00,\n"
        data = self._import_csv(csv_content)
        merchants = [t["merchant"] for t in data.get("transactions", [])]
        for m in merchants:
            self.assertNotIn("123-45-6789", m, f"SSN exposed in: {m}")

    def test_account_number_masked_in_response(self):
        csv_content = "date,merchant,amount,notes\n2024-01-01,ACCT 123456789012 FEE,-5.00,\n"
        data = self._import_csv(csv_content)
        merchants = [t["merchant"] for t in data.get("transactions", [])]
        for m in merchants:
            self.assertNotIn("123456789012", m, f"Account number exposed in: {m}")

    def test_normal_merchant_name_unchanged(self):
        self.assertEqual(mask_sensitive("WHOLE FOODS MARKET"), "WHOLE FOODS MARKET")

    def test_empty_string_passthrough(self):
        self.assertEqual(mask_sensitive(""), "")

    def test_none_passthrough(self):
        self.assertIsNone(mask_sensitive(None))

    def test_mask_sensitive_card_number(self):
        result = mask_sensitive("Card 4111111111111111 charge")
        self.assertNotIn("4111111111111111", result)

    def test_mask_sensitive_ssn(self):
        result = mask_sensitive("SSN 123-45-6789")
        self.assertNotIn("123-45-6789", result)


class TestDeduplication(unittest.TestCase):

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        flask_app._session["categories"].clear()
        flask_app._session["user_transactions"].clear()

    def test_dedup_empty_list(self):
        self.assertEqual(flask_app._dedup([]), [])

    def test_dedup_no_duplicates_unchanged(self):
        txns = [
            {"date": "2024-01-01", "merchant": "STORE A", "amount": -10.00},
            {"date": "2024-01-02", "merchant": "STORE B", "amount": -20.00},
            {"date": "2024-01-03", "merchant": "STORE C", "amount": -30.00},
        ]
        result = flask_app._dedup(txns)
        self.assertEqual(len(result), 3)

    def test_dedup_exact_duplicate_removed(self):
        t = {"date": "2024-01-01", "merchant": "WHOLE FOODS", "amount": -89.47}
        result = flask_app._dedup([t, t])
        self.assertEqual(len(result), 1)

    def test_dedup_keeps_first_occurrence(self):
        t1 = {"date": "2024-01-01", "merchant": "WHOLE FOODS", "amount": -89.47, "extra": "first"}
        t2 = {"date": "2024-01-01", "merchant": "WHOLE FOODS", "amount": -89.47, "extra": "second"}
        result = flask_app._dedup([t1, t2])
        self.assertEqual(result[0]["extra"], "first")

    def test_dedup_case_insensitive_merchant(self):
        t1 = {"date": "2024-01-01", "merchant": "whole foods", "amount": -89.47}
        t2 = {"date": "2024-01-01", "merchant": "WHOLE FOODS", "amount": -89.47}
        result = flask_app._dedup([t1, t2])
        self.assertEqual(len(result), 1)

    def test_dedup_merchant_whitespace_normalized(self):
        t1 = {"date": "2024-01-01", "merchant": "  WHOLE FOODS  ", "amount": -89.47}
        t2 = {"date": "2024-01-01", "merchant": "WHOLE FOODS",     "amount": -89.47}
        result = flask_app._dedup([t1, t2])
        self.assertEqual(len(result), 1)

    def test_dedup_different_date_not_deduplicated(self):
        t1 = {"date": "2024-01-01", "merchant": "WHOLE FOODS", "amount": -89.47}
        t2 = {"date": "2024-01-02", "merchant": "WHOLE FOODS", "amount": -89.47}
        result = flask_app._dedup([t1, t2])
        self.assertEqual(len(result), 2)

    def test_dedup_different_amount_not_deduplicated(self):
        t1 = {"date": "2024-01-01", "merchant": "WHOLE FOODS", "amount": -89.47}
        t2 = {"date": "2024-01-01", "merchant": "WHOLE FOODS", "amount": -89.48}
        result = flask_app._dedup([t1, t2])
        self.assertEqual(len(result), 2)

    def test_dedup_charge_and_credit_not_deduplicated(self):
        charge = {"date": "2024-01-01", "merchant": "AMAZON", "amount": -50.00}
        credit = {"date": "2024-01-01", "merchant": "AMAZON", "amount":  50.00}
        result = flask_app._dedup([charge, credit])
        self.assertEqual(len(result), 2)

    def test_dedup_amount_rounded_to_two_decimals(self):
        t1 = {"date": "2024-01-01", "merchant": "STORE", "amount": -50.004}
        t2 = {"date": "2024-01-01", "merchant": "STORE", "amount": -50.001}
        result = flask_app._dedup([t1, t2])
        self.assertEqual(len(result), 1)

    def test_dedup_multiple_copies_collapse_to_one(self):
        t = {"date": "2024-01-01", "merchant": "NETFLIX", "amount": -15.99}
        result = flask_app._dedup([t, t, t, t])
        self.assertEqual(len(result), 1)

    def test_dedup_preserves_order_of_unique_items(self):
        txns = [
            {"date": "2024-01-03", "merchant": "C", "amount": -3.0},
            {"date": "2024-01-01", "merchant": "A", "amount": -1.0},
            {"date": "2024-01-02", "merchant": "B", "amount": -2.0},
        ]
        result = flask_app._dedup(txns)
        self.assertEqual([t["merchant"] for t in result], ["C", "A", "B"])

    def test_get_transactions_returns_200(self):
        r = self.client.get("/transactions")
        self.assertEqual(r.status_code, 200)

    def test_get_transactions_response_shape(self):
        r = self.client.get("/transactions")
        data = json.loads(r.data)
        self.assertIn("transactions", data)
        self.assertIn("count", data)
        self.assertIsInstance(data["transactions"], list)
        self.assertIsInstance(data["count"], int)

    def test_get_transactions_count_matches_list_length(self):
        r = self.client.get("/transactions")
        data = json.loads(r.data)
        self.assertEqual(data["count"], len(data["transactions"]))

    def test_get_transactions_includes_session_imports(self):
        t = {"date": "2024-06-01", "merchant": "SESSION IMPORT", "amount": -99.00,
             "category": "Uncategorized", "account": "test", "source_file": "t.csv", "txn_id": "abc123"}
        flask_app._session["user_transactions"].append(t)

        r = self.client.get("/transactions")
        data = json.loads(r.data)
        merchants = [x["merchant"] for x in data["transactions"]]
        self.assertIn("SESSION IMPORT", merchants)

    def test_get_transactions_deduplicates_across_sources(self):
        t = {"date": "2024-06-15", "merchant": "DEDUP TEST MERCHANT", "amount": -42.00,
             "category": "Uncategorized", "account": "test", "source_file": "t.csv", "txn_id": "dup001"}
        flask_app._session["user_transactions"].extend([t, t])

        r = self.client.get("/transactions")
        data = json.loads(r.data)
        matching = [x for x in data["transactions"] if x["merchant"] == "DEDUP TEST MERCHANT"]
        self.assertEqual(len(matching), 1)

    def test_get_transactions_session_cleared_after_setup(self):
        r = self.client.get("/transactions")
        data = json.loads(r.data)
        session_count = len(flask_app._session["user_transactions"])
        self.assertEqual(session_count, 0)


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestPersistence(unittest.TestCase):
    """Verify SQLite persistence across simulated restarts (reload from DB)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_db_path = flask_app.DB_PATH
        # Use a fresh in-memory-like DB for each test (temp file, deleted on tearDown)
        flask_app.DB_PATH = Path(self.tmpdir) / "test_state.db"
        flask_app._init_db()
        # Clear in-memory session
        flask_app._session["categories"].clear()
        flask_app._session["custom_categories"].clear()
        flask_app._session["user_transactions"].clear()

    def tearDown(self):
        flask_app.DB_PATH = self._orig_db_path
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _reload_session(self):
        """Simulate a server restart by clearing memory and reloading from DB."""
        flask_app._session["categories"].clear()
        flask_app._session["custom_categories"].clear()
        flask_app._session["user_transactions"].clear()
        flask_app._load_session_from_db()

    def test_db_init_creates_tables(self):
        import sqlite3
        conn = sqlite3.connect(flask_app.DB_PATH)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        self.assertIn("category_overrides", tables)
        self.assertIn("custom_categories", tables)
        self.assertIn("user_transactions", tables)
        self.assertIn("learned_rules", tables)

    def test_category_override_persists(self):
        flask_app._db_save_category("txn001", "Groceries")
        self._reload_session()
        self.assertEqual(flask_app._session["categories"].get("txn001"), "Groceries")

    def test_category_override_upsert(self):
        flask_app._db_save_category("txn002", "Shopping")
        flask_app._db_save_category("txn002", "Food & Dining")
        self._reload_session()
        self.assertEqual(flask_app._session["categories"]["txn002"], "Food & Dining")

    def test_multiple_category_overrides_all_reload(self):
        flask_app._db_save_category("a", "Groceries")
        flask_app._db_save_category("b", "Travel")
        flask_app._db_save_category("c", "Shopping")
        self._reload_session()
        self.assertEqual(flask_app._session["categories"]["a"], "Groceries")
        self.assertEqual(flask_app._session["categories"]["b"], "Travel")
        self.assertEqual(flask_app._session["categories"]["c"], "Shopping")

    def test_custom_category_persists(self):
        flask_app._db_save_custom_category("Pets")
        self._reload_session()
        self.assertIn("Pets", flask_app._session["custom_categories"])

    def test_custom_category_remove_persists(self):
        flask_app._db_save_custom_category("Temporary")
        flask_app._db_remove_custom_category("Temporary")
        self._reload_session()
        self.assertNotIn("Temporary", flask_app._session["custom_categories"])

    def test_multiple_custom_categories_order_preserved(self):
        for name in ["Alpha", "Beta", "Gamma"]:
            flask_app._db_save_custom_category(name)
        self._reload_session()
        self.assertEqual(flask_app._session["custom_categories"], ["Alpha", "Beta", "Gamma"])

    def test_user_transactions_persist(self):
        txns = [
            {"txn_id": "tx001", "date": "2024-01-15", "merchant": "WHOLE FOODS",
             "amount": -89.47, "category": "Groceries", "account": "Chase ••1234",
             "source_file": "chase.csv", "source_folder": "expense"},
        ]
        flask_app._db_save_user_transactions(txns)
        self._reload_session()
        self.assertEqual(len(flask_app._session["user_transactions"]), 1)
        self.assertEqual(flask_app._session["user_transactions"][0]["txn_id"], "tx001")
        self.assertAlmostEqual(flask_app._session["user_transactions"][0]["amount"], -89.47)

    def test_user_transactions_no_duplicate_insert(self):
        txn = {"txn_id": "tx_dup", "date": "2024-02-01", "merchant": "NETFLIX",
               "amount": -15.99, "category": "Subscriptions", "account": "Chase ••1234",
               "source_file": "chase.csv", "source_folder": "expense"}
        flask_app._db_save_user_transactions([txn])
        flask_app._db_save_user_transactions([txn])  # second insert should be ignored
        self._reload_session()
        self.assertEqual(len(flask_app._session["user_transactions"]), 1)

    def test_user_transactions_multiple_persist(self):
        txns = [
            {"txn_id": f"t{i}", "date": f"2024-01-{i+1:02d}", "merchant": f"STORE {i}",
             "amount": float(-i * 10), "category": "Shopping", "account": "test",
             "source_file": "test.csv", "source_folder": "expense"}
            for i in range(1, 6)
        ]
        flask_app._db_save_user_transactions(txns)
        self._reload_session()
        self.assertEqual(len(flask_app._session["user_transactions"]), 5)

    def test_txn_id_queryable_in_db(self):
        """Verify txn_id is stored as a top-level column (not just inside JSON)."""
        import sqlite3
        txn = {"txn_id": "qry001", "date": "2024-03-01", "merchant": "TEST",
               "amount": -25.00, "source_file": "f.csv", "source_folder": "expense"}
        flask_app._db_save_user_transactions([txn])
        conn = sqlite3.connect(flask_app.DB_PATH)
        row = conn.execute(
            "SELECT txn_id, source_file FROM user_transactions WHERE txn_id = ?", ("qry001",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "qry001")
        self.assertEqual(row[1], "f.csv")

    def test_custom_category_id_autoincrement(self):
        """Verify custom_categories table has auto-increment ids."""
        import sqlite3
        for name in ["X", "Y", "Z"]:
            flask_app._db_save_custom_category(name)
        conn = sqlite3.connect(flask_app.DB_PATH)
        rows = conn.execute("SELECT id, name FROM custom_categories ORDER BY id").fetchall()
        conn.close()
        ids = [r[0] for r in rows]
        self.assertEqual(ids, sorted(ids))  # ids are sequential / ascending
        self.assertGreater(ids[-1], 0)


class TestLearnedRules(unittest.TestCase):
    """Verify the category learning algorithm — pattern extraction, persistence, and propagation."""

    def setUp(self):
        import sqlite3 as _sqlite3
        self._sqlite3 = _sqlite3
        self.tmpdir = tempfile.mkdtemp()
        self._orig_db_path = flask_app.DB_PATH
        flask_app.DB_PATH = Path(self.tmpdir) / "test_rules.db"
        flask_app._init_db()
        flask_app._session["categories"].clear()
        flask_app._session["custom_categories"].clear()
        flask_app._session["user_transactions"].clear()
        flask_app._session["learned_rules"] = []
        self.client = flask_app.app.test_client()

    def tearDown(self):
        flask_app.DB_PATH = self._orig_db_path
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _reload_session(self):
        flask_app._session["categories"].clear()
        flask_app._session["custom_categories"].clear()
        flask_app._session["user_transactions"].clear()
        flask_app._session["learned_rules"] = []
        flask_app._load_session_from_db()

    # ── Pattern extraction ─────────────────────────────────────────────────────

    def test_extract_pattern_strips_pos_prefix(self):
        self.assertEqual(flask_app._extract_pattern("SQ *SURYA DARSHINI DOS"), "SURYA DARSHINI")

    def test_extract_pattern_strips_store_number(self):
        result = flask_app._extract_pattern("WHOLEFDS RSQ 10103")
        # After stripping 5-digit "10103" and trailing short code "RSQ", should be "WHOLEFDS"
        self.assertNotIn("10103", result)
        self.assertTrue(result.startswith("WHOLEFDS"))

    def test_extract_pattern_strips_dash_location(self):
        self.assertEqual(flask_app._extract_pattern("TST*MYLAPORE - MILPITA"), "MYLAPORE")

    def test_extract_pattern_strips_dot_domain(self):
        self.assertEqual(flask_app._extract_pattern("NETFLIX.COM"), "NETFLIX COM")

    # ── Persistence ────────────────────────────────────────────────────────────

    def test_learned_rule_persists(self):
        flask_app._db_save_learned_rule("WHOLEFDS", "Groceries")
        self._reload_session()
        rules = flask_app._session["learned_rules"]
        self.assertTrue(any(r["pattern"] == "WHOLEFDS" and r["category"] == "Groceries" for r in rules))

    def test_learned_rule_upsert_updates_category(self):
        flask_app._db_save_learned_rule("WHOLEFDS", "Shopping")
        flask_app._db_save_learned_rule("WHOLEFDS", "Groceries")
        self._reload_session()
        rules = flask_app._session["learned_rules"]
        match = next(r for r in rules if r["pattern"] == "WHOLEFDS")
        self.assertEqual(match["category"], "Groceries")

    def test_learned_rule_hit_count_increments(self):
        flask_app._db_save_learned_rule("NETFLIX", "Subscriptions")
        flask_app._db_save_learned_rule("NETFLIX", "Subscriptions")
        conn = self._sqlite3.connect(flask_app.DB_PATH)
        row = conn.execute("SELECT hit_count FROM learned_rules WHERE pattern = 'NETFLIX'").fetchone()
        conn.close()
        self.assertEqual(row[0], 2)

    def test_learned_rules_loaded_on_startup(self):
        flask_app._db_save_learned_rule("AMAZON", "Shopping")
        flask_app._db_save_learned_rule("STARBUCKS", "Food & Dining")
        self._reload_session()
        patterns = {r["pattern"] for r in flask_app._session["learned_rules"]}
        self.assertIn("AMAZON", patterns)
        self.assertIn("STARBUCKS", patterns)

    # ── Endpoints ─────────────────────────────────────────────────────────────

    def test_categories_update_returns_pattern(self):
        res = self.client.post("/categories/update", json={
            "txn_id": "abc123", "category": "Groceries", "merchant": "WHOLEFDS RSQ 10103"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertIn("pattern", data)
        self.assertTrue(len(data["pattern"]) > 0)

    def test_db_init_creates_learned_rules_table(self):
        conn = self._sqlite3.connect(flask_app.DB_PATH)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        self.assertIn("learned_rules", tables)

    def test_batch_update_saves_all_overrides(self):
        updates = [
            {"txn_id": "t1", "category": "Groceries"},
            {"txn_id": "t2", "category": "Shopping"},
            {"txn_id": "t3", "category": "Travel"},
        ]
        res = self.client.post("/categories/batch-update", json={"updates": updates})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["updated"], 3)
        self.assertTrue(data["ok"])
        # Verify all three are in the DB
        conn = self._sqlite3.connect(flask_app.DB_PATH)
        saved = {r[0]: r[1] for r in conn.execute(
            "SELECT txn_id, category FROM category_overrides"
        ).fetchall()}
        conn.close()
        self.assertEqual(saved.get("t1"), "Groceries")
        self.assertEqual(saved.get("t2"), "Shopping")
        self.assertEqual(saved.get("t3"), "Travel")

    def test_get_learned_rules_endpoint(self):
        flask_app._db_save_learned_rule("NETFLIX", "Subscriptions")
        flask_app._session["learned_rules"] = flask_app._db_load_learned_rules()
        res = self.client.get("/categories/rules")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("rules", data)
        self.assertTrue(any(r["pattern"] == "NETFLIX" for r in data["rules"]))


class TestChatContextBuilding(unittest.TestCase):
    """Test that the /chat endpoint correctly builds the system prompt from frontend data."""

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        self.captured_messages = None

    def _make_payload(self, message="How much did I spend?", **overrides):
        data = {
            "monthlyTrend": [
                {"month": "2026-03", "expenses": 500.00, "income": 3200.00, "net": 2700.00,
                 "categories": [{"category": "Groceries", "total": 300.00}, {"category": "Gas & Fuel", "total": 200.00}],
                 "merchants": [{"merchant": "WHOLE FOODS", "total": 200.00}, {"merchant": "SHELL OIL", "total": 200.00}]},
                {"month": "2026-04", "expenses": 153.89, "income": 3200.00, "net": 3046.11,
                 "categories": [{"category": "Groceries", "total": 88.30}, {"category": "Subscriptions", "total": 15.99}, {"category": "Gas & Fuel", "total": 49.60}],
                 "merchants": [{"merchant": "WHOLE FOODS", "total": 88.30}, {"merchant": "SHELL OIL", "total": 49.60}, {"merchant": "NETFLIX.COM", "total": 15.99}]},
            ],
            "categories": ["Groceries", "Subscriptions", "Gas & Fuel", "Uncategorized"],
        }
        data.update(overrides)
        return {"message": message, "history": [], "data": data}

    def _post_chat_and_capture(self, payload):
        """Post to /chat with mocked llama-server, return captured system prompt."""
        from unittest.mock import patch, MagicMock

        sse_body = b"data: {\"choices\":[{\"delta\":{\"content\":\"OK\"}}]}\n\ndata: [DONE]\n\n"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines = MagicMock(return_value=iter(sse_body.split(b"\n")))

        with patch("requests.post", return_value=mock_resp) as mock_post:
            r = self.client.post("/chat",
                                 data=json.dumps(payload),
                                 content_type="application/json")
            self.assertEqual(r.status_code, 200)
            if mock_post.called:
                call_kwargs = mock_post.call_args[1] if mock_post.call_args[1] else {}
                call_json = call_kwargs.get("json", {})
                self.captured_messages = call_json.get("messages", [])
            return r

    def test_system_prompt_includes_merchants_per_month(self):
        """Merchants from each month appear in the system prompt."""
        payload = self._make_payload()
        self._post_chat_and_capture(payload)
        system = self.captured_messages[0]["content"]
        self.assertIn("WHOLE FOODS", system)
        self.assertIn("NETFLIX.COM", system)
        self.assertIn("SHELL OIL", system)

    def test_system_prompt_includes_categories_per_month(self):
        """Category breakdowns from each month appear in the system prompt."""
        payload = self._make_payload()
        self._post_chat_and_capture(payload)
        system = self.captured_messages[0]["content"]
        self.assertIn("Groceries", system)
        self.assertIn("Subscriptions", system)
        self.assertIn("Gas & Fuel", system)
        self.assertIn("88.30", system)

    def test_system_prompt_includes_monthly_totals(self):
        """Monthly expense/income totals appear in the system prompt."""
        payload = self._make_payload()
        self._post_chat_and_capture(payload)
        system = self.captured_messages[0]["content"]
        self.assertIn("2026-03", system)
        self.assertIn("2026-04", system)
        self.assertIn("500.00", system)
        self.assertIn("153.89", system)

    def test_system_prompt_includes_today_date(self):
        """System prompt contains today's date for temporal reasoning."""
        payload = self._make_payload()
        self._post_chat_and_capture(payload)
        system = self.captured_messages[0]["content"]
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.assertIn(today_str, system)

    def test_system_prompt_includes_available_categories(self):
        """Available categories list is in the system prompt."""
        payload = self._make_payload()
        self._post_chat_and_capture(payload)
        system = self.captured_messages[0]["content"]
        self.assertIn("Groceries", system)
        self.assertIn("Subscriptions", system)

    def test_history_included_in_messages(self):
        """Conversation history is passed to the LLM."""
        payload = self._make_payload()
        payload["history"] = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        self._post_chat_and_capture(payload)
        self.assertIsNotNone(self.captured_messages)
        self.assertEqual(len(self.captured_messages), 4)
        self.assertEqual(self.captured_messages[1]["content"], "Hello")
        self.assertEqual(self.captured_messages[2]["content"], "Hi there!")

    def test_user_message_is_last_in_messages(self):
        """The current user message is the last entry in messages."""
        payload = self._make_payload(message="What are my top expenses?")
        self._post_chat_and_capture(payload)
        last = self.captured_messages[-1]
        self.assertEqual(last["role"], "user")
        self.assertEqual(last["content"], "What are my top expenses?")

    def test_empty_data_shows_no_data_placeholders(self):
        """When frontend sends empty arrays, prompt shows '(no data)' placeholders."""
        payload = {"message": "Hello", "history": [], "data": {"monthlyTrend": [], "categories": []}}
        self._post_chat_and_capture(payload)
        system = self.captured_messages[0]["content"]
        self.assertIn("(no data)", system)

    def test_multiple_months_all_present(self):
        """All months with their breakdowns appear in the prompt."""
        months = [
            {"month": f"2026-0{i}", "expenses": i * 100.0, "income": 3000.0, "net": 3000.0 - i * 100.0,
             "categories": [{"category": "Food", "total": i * 50.0}],
             "merchants": [{"merchant": f"STORE_{i}", "total": i * 50.0}]}
            for i in range(1, 5)
        ]
        payload = self._make_payload(monthlyTrend=months)
        self._post_chat_and_capture(payload)
        system = self.captured_messages[0]["content"]
        for m in months:
            self.assertIn(m["month"], system)
            self.assertIn(m["merchants"][0]["merchant"], system)

    def test_chat_returns_400_for_empty_message(self):
        """Empty or whitespace-only message returns 400."""
        for msg in ["", "   ", None]:
            payload = {"message": msg, "history": [], "data": {}}
            r = self.client.post("/chat",
                                 data=json.dumps(payload),
                                 content_type="application/json")
            self.assertEqual(r.status_code, 400)

    def test_chat_returns_400_for_missing_body(self):
        """No JSON body returns 400."""
        r = self.client.post("/chat", data="", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_history_truncated_to_10_turns(self):
        """Only the last 10 history turns are included."""
        history = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
        payload = self._make_payload()
        payload["history"] = history
        self._post_chat_and_capture(payload)
        self.assertEqual(len(self.captured_messages), 12)
        self.assertEqual(self.captured_messages[1]["content"], "msg10")

    def test_merchant_totals_formatted_with_dollar_amounts(self):
        """Merchant totals show dollar formatting in the prompt."""
        payload = self._make_payload(monthlyTrend=[
            {"month": "2026-04", "expenses": 1234.56, "income": 0, "net": -1234.56,
             "categories": [], "merchants": [{"merchant": "COSTCO", "total": 1234.56}]}
        ])
        self._post_chat_and_capture(payload)
        system = self.captured_messages[0]["content"]
        self.assertIn("$1,234.56", system)

    def test_month_without_breakdowns_still_shows_totals(self):
        """A month with empty categories/merchants still shows expense totals."""
        payload = self._make_payload(monthlyTrend=[
            {"month": "2026-01", "expenses": 250.00, "income": 3000.00, "net": 2750.00,
             "categories": [], "merchants": []}
        ])
        self._post_chat_and_capture(payload)
        system = self.captured_messages[0]["content"]
        self.assertIn("2026-01", system)
        self.assertIn("250.00", system)

    def test_llm_has_data_for_follow_up_questions(self):
        """Both months' data is available so the LLM can handle follow-ups about any period."""
        payload = self._make_payload()
        self._post_chat_and_capture(payload)
        system = self.captured_messages[0]["content"]
        # March data present (for "last month" follow-ups)
        self.assertIn("2026-03", system)
        self.assertIn("300.00", system)  # March groceries
        # April data present (for "this month" questions)
        self.assertIn("2026-04", system)
        self.assertIn("88.30", system)   # April groceries


if __name__ == "__main__":
    unittest.main()
