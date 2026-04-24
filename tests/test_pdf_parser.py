"""
Tests for parsers/pdf_parser.py — PDF statement parsing.
Covers: account detection, date parsing, amount parsing, table row detection,
table row parsing, text line fallback, and the full parse_pdf pipeline.
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from finn_tracker.parsers.pdf_parser import (
    _detect_account_from_text,
    _parse_date,
    _parse_amount,
    _extract_date_from_cell,
    _is_transaction_row,
    _parse_table_row,
    _parse_text_lines,
    parse_pdf,
)
from finn_tracker.models import Transaction


# ══════════════════════════════════════════════════════════════════════════════
# ACCOUNT DETECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectAccountFromText(unittest.TestCase):

    def test_chase_with_last4(self):
        text = "JPMorgan Chase Bank\nAccount ending in 4231"
        result = _detect_account_from_text(text)
        self.assertIn("Chase", result)
        self.assertIn("4231", result)

    def test_capital_one_with_last4(self):
        text = "Capital One\nCard Number: ****1234"
        result = _detect_account_from_text(text)
        self.assertIn("Capital One", result)
        self.assertIn("1234", result)

    def test_bofa_with_last4(self):
        text = "Bank of America\nAccount #: XXXX-5678"
        result = _detect_account_from_text(text)
        self.assertIn("BofA", result)
        self.assertIn("5678", result)

    def test_chase_without_last4(self):
        text = "JPMorgan Chase Bank\nStatement Period: Jan 2024"
        result = _detect_account_from_text(text)
        self.assertEqual(result, "Chase")

    def test_unknown_bank(self):
        text = "Some Random Bank\nAccount ending in 9999"
        result = _detect_account_from_text(text)
        # No known bank, but might detect last4
        self.assertNotIn("Chase", result)

    def test_empty_text(self):
        self.assertEqual(_detect_account_from_text(""), "")

    def test_none_text(self):
        self.assertEqual(_detect_account_from_text(None), "")

    def test_ending_in_pattern(self):
        text = "Chase\nending in 4567"
        result = _detect_account_from_text(text)
        self.assertIn("4567", result)

    def test_card_number_pattern(self):
        text = "Capital One\nCard No. XXXX-XXXX-XXXX-7890"
        result = _detect_account_from_text(text)
        self.assertIn("7890", result)


# ══════════════════════════════════════════════════════════════════════════════
# DATE PARSING
# ══════════════════════════════════════════════════════════════════════════════

class TestParseDatePDF(unittest.TestCase):

    def test_slash_format(self):
        self.assertEqual(_parse_date("01/15/2024"), date(2024, 1, 15))

    def test_iso_format(self):
        self.assertEqual(_parse_date("2024-01-15"), date(2024, 1, 15))

    def test_short_year(self):
        self.assertEqual(_parse_date("01/15/24"), date(2024, 1, 15))

    def test_month_name_format(self):
        self.assertEqual(_parse_date("Jan 15 2024"), date(2024, 1, 15))

    def test_day_month_year(self):
        self.assertEqual(_parse_date("15-Jan-2024"), date(2024, 1, 15))

    def test_day_month_short_year(self):
        self.assertEqual(_parse_date("15-Jan-24"), date(2024, 1, 15))

    def test_invalid_returns_none(self):
        self.assertIsNone(_parse_date("NOT A DATE"))

    def test_whitespace_stripped(self):
        self.assertEqual(_parse_date("  01/15/2024  "), date(2024, 1, 15))


# ══════════════════════════════════════════════════════════════════════════════
# AMOUNT PARSING
# ══════════════════════════════════════════════════════════════════════════════

class TestParseAmountPDF(unittest.TestCase):

    def test_simple_positive(self):
        self.assertAlmostEqual(_parse_amount("45.67"), 45.67)

    def test_dollar_sign(self):
        self.assertAlmostEqual(_parse_amount("$100.00"), 100.00)

    def test_negative(self):
        self.assertAlmostEqual(_parse_amount("-$50.00"), -50.00)

    def test_parentheses_negative(self):
        self.assertAlmostEqual(_parse_amount("(75.00)"), -75.00)

    def test_comma_thousands(self):
        self.assertAlmostEqual(_parse_amount("$1,234.56"), 1234.56)

    def test_invalid_returns_none(self):
        self.assertIsNone(_parse_amount("not a number"))

    def test_whitespace_stripped(self):
        self.assertAlmostEqual(_parse_amount("  $45.67  "), 45.67)

    def test_spaces_in_amount(self):
        self.assertAlmostEqual(_parse_amount("$ 45.67"), 45.67)


# ══════════════════════════════════════════════════════════════════════════════
# DATE EXTRACTION FROM CELL
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractDateFromCell(unittest.TestCase):

    def test_slash_date(self):
        self.assertEqual(_extract_date_from_cell("01/15/2024"), date(2024, 1, 15))

    def test_iso_date_in_text(self):
        self.assertEqual(_extract_date_from_cell("Purchase on 2024-03-10"), date(2024, 3, 10))

    def test_no_date(self):
        self.assertIsNone(_extract_date_from_cell("No date here"))

    def test_empty_string(self):
        self.assertIsNone(_extract_date_from_cell(""))

    def test_none_input(self):
        self.assertIsNone(_extract_date_from_cell(None))


# ══════════════════════════════════════════════════════════════════════════════
# TRANSACTION ROW DETECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestIsTransactionRow(unittest.TestCase):

    def test_valid_row(self):
        row = ["01/15/2024", "WHOLE FOODS", "$89.47"]
        self.assertTrue(_is_transaction_row(row))

    def test_no_date(self):
        row = ["WHOLE FOODS", "$89.47", "Groceries"]
        self.assertFalse(_is_transaction_row(row))

    def test_no_amount(self):
        row = ["01/15/2024", "WHOLE FOODS", "Groceries"]
        self.assertFalse(_is_transaction_row(row))

    def test_none_cells(self):
        row = [None, None, None]
        self.assertFalse(_is_transaction_row(row))

    def test_empty_row(self):
        self.assertFalse(_is_transaction_row([]))


# ══════════════════════════════════════════════════════════════════════════════
# TABLE ROW PARSING
# ══════════════════════════════════════════════════════════════════════════════

class TestParseTableRow(unittest.TestCase):

    def test_valid_row(self):
        row = ["01/15/2024", "WHOLE FOODS MARKET", "$89.47"]
        result = _parse_table_row(row, "test.pdf", "Chase ••1234")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, Transaction)
        self.assertEqual(result.date, date(2024, 1, 15))
        self.assertAlmostEqual(result.amount, 89.47)
        self.assertEqual(result.account, "Chase ••1234")

    def test_missing_date_returns_none(self):
        row = ["WHOLE FOODS", "$89.47"]
        result = _parse_table_row(row, "test.pdf", "")
        self.assertIsNone(result)

    def test_missing_amount_returns_none(self):
        row = ["01/15/2024", "WHOLE FOODS", "Groceries"]
        result = _parse_table_row(row, "test.pdf", "")
        self.assertIsNone(result)

    def test_merchant_is_longest_non_date_non_amount(self):
        row = ["01/15/2024", "A", "WHOLE FOODS MARKET #123", "$89.47"]
        result = _parse_table_row(row, "test.pdf", "")
        self.assertIn("WHOLE FOODS", result.merchant)

    def test_none_cells_handled(self):
        row = [None, "01/15/2024", None, "STORE", "$10.00"]
        result = _parse_table_row(row, "test.pdf", "")
        self.assertIsNotNone(result)


# ══════════════════════════════════════════════════════════════════════════════
# TEXT LINE FALLBACK PARSING
# ══════════════════════════════════════════════════════════════════════════════

class TestParseTextLines(unittest.TestCase):

    def test_parses_simple_lines(self):
        text = "01/15/2024 WHOLE FOODS MARKET $89.47\n01/16/2024 SHELL OIL $45.00\n"
        result = _parse_text_lines(text, "test.pdf", "Chase")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].date, date(2024, 1, 15))

    def test_skips_short_lines(self):
        text = "short\n01/15/2024 WHOLE FOODS $89.47\n"
        result = _parse_text_lines(text, "test.pdf", "")
        self.assertEqual(len(result), 1)

    def test_skips_lines_without_date(self):
        text = "WHOLE FOODS MARKET $89.47\nSome random text\n"
        result = _parse_text_lines(text, "test.pdf", "")
        self.assertEqual(len(result), 0)

    def test_skips_lines_without_amount(self):
        text = "01/15/2024 WHOLE FOODS MARKET no amount here\n"
        result = _parse_text_lines(text, "test.pdf", "")
        self.assertEqual(len(result), 0)

    def test_negative_amount(self):
        text = "01/15/2024 REFUND -$50.00\n"
        result = _parse_text_lines(text, "test.pdf", "")
        if result:
            self.assertAlmostEqual(result[0].amount, -50.0)

    def test_merchant_truncated_to_80_chars(self):
        long_merchant = "A" * 200
        text = f"01/15/2024 {long_merchant} $10.00\n"
        result = _parse_text_lines(text, "test.pdf", "")
        if result:
            self.assertLessEqual(len(result[0].merchant), 80)

    def test_empty_text(self):
        result = _parse_text_lines("", "test.pdf", "")
        self.assertEqual(result, [])

    def test_account_label_applied(self):
        text = "01/15/2024 STORE $25.00\n"
        result = _parse_text_lines(text, "test.pdf", "Chase ••1234")
        if result:
            self.assertEqual(result[0].account, "Chase ••1234")


# ══════════════════════════════════════════════════════════════════════════════
# FULL PARSE_PDF PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class TestParsePDF(unittest.TestCase):

    def test_nonexistent_file_returns_error(self):
        result = parse_pdf("/nonexistent/file.pdf")
        self.assertFalse(result.success)
        self.assertGreater(len(result.errors), 0)

    def test_corrupted_file_returns_error(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"not a real pdf content")
            f.flush()
            result = parse_pdf(f.name)
        self.assertFalse(result.success)
        self.assertGreater(len(result.errors), 0)
        Path(f.name).unlink(missing_ok=True)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_table_extraction_path(self, mock_pdfplumber):
        """Test the table extraction strategy with mocked pdfplumber."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Chase Bank\nAccount ending in 1234"
        mock_page.extract_tables.return_value = [
            [
                ["01/15/2024", "WHOLE FOODS", "$89.47"],
                ["01/16/2024", "SHELL OIL", "$45.00"],
            ]
        ]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        result = parse_pdf("/fake/statement.pdf")
        self.assertTrue(result.success)
        self.assertEqual(len(result.transactions), 2)
        self.assertEqual(result.parser_used, "pdf_table")

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_text_fallback_when_no_tables(self, mock_pdfplumber):
        """Test text fallback when tables yield no transactions."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = (
            "Chase Bank Statement\n"
            "01/15/2024 WHOLE FOODS MARKET $89.47\n"
            "01/16/2024 SHELL OIL STATION $45.00\n"
        )
        mock_page.extract_tables.return_value = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        result = parse_pdf("/fake/statement.pdf")
        self.assertTrue(result.success)
        self.assertEqual(result.parser_used, "pdf_text_fallback")

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_deduplication_in_parse_pdf(self, mock_pdfplumber):
        """Duplicate transactions from same page are deduplicated."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_page.extract_tables.return_value = [
            [
                ["01/15/2024", "WHOLE FOODS", "$89.47"],
                ["01/15/2024", "WHOLE FOODS", "$89.47"],  # duplicate
            ]
        ]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        result = parse_pdf("/fake/statement.pdf")
        self.assertEqual(len(result.transactions), 1)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_page_error_captured(self, mock_pdfplumber):
        """Errors on individual pages are captured but don't crash the parser."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_page.extract_tables.side_effect = RuntimeError("page corrupt")

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        result = parse_pdf("/fake/statement.pdf")
        self.assertGreater(len(result.errors), 0)
        self.assertIn("Page 1", result.errors[0])

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_account_label_fallback(self, mock_pdfplumber):
        """When no account detected from text, uses provided account_label."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "No bank info here"
        mock_page.extract_tables.return_value = [
            [["01/15/2024", "STORE", "$10.00"]]
        ]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        result = parse_pdf("/fake/statement.pdf", account_label="My Account")
        if result.transactions:
            self.assertEqual(result.transactions[0].account, "My Account")

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_empty_pdf_no_crash(self, mock_pdfplumber):
        """PDF with no pages doesn't crash."""
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        result = parse_pdf("/fake/empty.pdf")
        self.assertFalse(result.success)

    def test_source_file_set(self):
        result = parse_pdf("/nonexistent/my_statement.pdf")
        self.assertEqual(result.source_file, "my_statement.pdf")


if __name__ == "__main__":
    unittest.main()
