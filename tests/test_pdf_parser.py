"""
Tests for parsers/pdf_parser.py — PDF statement parsing.
Covers: account detection, date parsing, amount parsing, table row detection,
table row parsing, text line fallback, and the full parse_pdf pipeline.
"""
import shutil
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
    _parse_short_date,
    _parse_numeric_short_date,
    _extract_date_from_cell,
    _is_transaction_row,
    _parse_table_row,
    _parse_text_lines,
    _collect_dates,
    _find_post_date_col,
    _detect_post_date_position,
    parse_pdf,
    SHORT_DATE_PATTERN,
    NUMERIC_SHORT_DATE_PATTERN,
    _SUMMARY_LINE_RE,
)
from finn_tracker.models import Transaction
from sample_data.generators import (
    write_sample_pdf_files,
    CAPITAL_ONE_PDF_TRANSACTION_COUNT,
    CHASE_PDF_TRANSACTION_COUNT,
    BOFA_PDF_TRANSACTION_COUNT,
)


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

    # Unicode minus variants — BofA and some other banks use these in PDFs
    def test_em_dash_negative(self):
        self.assertAlmostEqual(_parse_amount("—100.00"), -100.00)

    def test_en_dash_negative(self):
        self.assertAlmostEqual(_parse_amount("–287.20"), -287.20)

    def test_unicode_minus_sign(self):
        self.assertAlmostEqual(_parse_amount("−50.00"), -50.00)

    def test_em_dash_with_comma(self):
        self.assertAlmostEqual(_parse_amount("—3,987.42"), -3987.42)

    def test_em_dash_with_dollar(self):
        self.assertAlmostEqual(_parse_amount("—$115.75"), -115.75)


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


# ══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL ACCOUNT DETECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectAccountAdditional(unittest.TestCase):

    def test_unknown_bank_with_last4_returns_empty(self):
        # Unrecognised bank name → no label even if last4 is detectable
        text = "Mysterious Financial Corp\nAccount ending in 9999"
        self.assertEqual(_detect_account_from_text(text), "")

    def test_jpmorgan_alias_detected_as_chase(self):
        text = "JPMorgan\nAccount ending in 1234"
        result = _detect_account_from_text(text)
        self.assertIn("Chase", result)
        self.assertIn("1234", result)

    def test_bofa_full_unmasked_account_number_returns_last4(self):
        # BofA statements print the full account number (e.g. "1234 5678 9012 3456").
        # Greedy quantifier in Pattern 2 must backtrack to pick the LAST 4-digit group,
        # not the first ("1234").
        text = "Bank of America\nVisa Signature\nAccount# 1234 5678 9012 3456"
        result = _detect_account_from_text(text)
        self.assertIn("BofA", result)
        self.assertIn("3456", result)
        self.assertNotIn("1234", result)

    def test_bofa_masked_account_number(self):
        # Masked format "XXXX XXXX XXXX 9012" — greedy matching must still find "9012"
        text = "Bank of America\nVisa Signature\nAccount# XXXX XXXX XXXX 9012"
        result = _detect_account_from_text(text)
        self.assertIn("BofA", result)
        self.assertIn("9012", result)


# ══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL DATE PARSING
# ══════════════════════════════════════════════════════════════════════════════

class TestParseDatePDFAdditional(unittest.TestCase):

    def test_month_name_no_comma(self):
        # Exercises the %b %d %Y format (no comma between day and year)
        self.assertEqual(_parse_date("Jan 15 2024"), date(2024, 1, 15))


# ══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL DATE EXTRACTION FROM CELL
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractDateFromCellAdditional(unittest.TestCase):

    def test_month_name_format(self):
        self.assertEqual(_extract_date_from_cell("Jan 15, 2024"), date(2024, 1, 15))

    def test_date_embedded_in_description(self):
        # Date appears mid-string; extraction should still find it
        self.assertEqual(_extract_date_from_cell("Purchase 2024-03-10 at STORE"), date(2024, 3, 10))


# ══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL TABLE ROW PARSING
# ══════════════════════════════════════════════════════════════════════════════

class TestParseTableRowAdditional(unittest.TestCase):

    def test_rightmost_amount_taken_when_multiple_in_row(self):
        # Row has a balance column ($100.00) AND a transaction column (-$89.47).
        # The parser takes the last matching cell, so -$89.47 must win.
        row = ["01/15/2024", "Balance $100.00", "WHOLE FOODS", "-$89.47"]
        result = _parse_table_row(row, "test.pdf", "Chase ••1234")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.amount, -89.47)

    def test_returns_none_when_no_merchant_candidates(self):
        # Only a date cell and an amount cell — no merchant text found.
        # The row is likely a summary/header line, so the parser returns None
        # rather than emitting a fake transaction with merchant "Unknown".
        row = ["01/15/2024", "$89.47"]
        result = _parse_table_row(row, "test.pdf", "")
        self.assertIsNone(result)

    def test_raw_description_holds_original_merchant_text(self):
        # raw_description must be the pre-mask value; merchant is the masked output.
        # For a plain merchant name mask_sensitive is a no-op, so both are equal.
        row = ["01/15/2024", "STORE PURCHASE", "$10.00"]
        result = _parse_table_row(row, "test.pdf", "")
        self.assertIsNotNone(result)
        self.assertEqual(result.raw_description, "STORE PURCHASE")
        self.assertEqual(result.merchant, result.raw_description)


# ══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL TEXT-LINE FALLBACK PARSING
# ══════════════════════════════════════════════════════════════════════════════

class TestParseTextLinesAdditional(unittest.TestCase):

    def test_negative_amount_parsed_unconditionally(self):
        # Stronger version of the original test that used an 'if result:' guard
        text = "01/15/2024 REFUND -$50.00\n"
        result = _parse_text_lines(text, "test.pdf", "")
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].amount, -50.0)

    def test_merchant_truncated_to_80_chars_unconditionally(self):
        # Stronger version of the original test that used an 'if result:' guard
        long_merchant = "A" * 200
        text = f"01/15/2024 {long_merchant} $10.00\n"
        result = _parse_text_lines(text, "test.pdf", "")
        self.assertEqual(len(result), 1)
        self.assertLessEqual(len(result[0].merchant), 80)

    def test_multiple_amounts_takes_last(self):
        # Line contains a balance amount and a transaction amount; last must win
        text = "01/15/2024 Balance $500.00 WHOLE FOODS -$89.47\n"
        result = _parse_text_lines(text, "test.pdf", "")
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].amount, -89.47)

    def test_month_name_date_format_parsed(self):
        # Date in "Jan 15 2024" form triggers DATE_PATTERNS[2]
        text = "Jan 15 2024 COFFEE SHOP $5.25\n"
        result = _parse_text_lines(text, "test.pdf", "")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].date, date(2024, 1, 15))
        self.assertAlmostEqual(result[0].amount, 5.25)


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-PAGE AND PIPELINE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestParsePDFPipeline(unittest.TestCase):

    def _make_page(self, text="", tables=None):
        p = MagicMock()
        p.extract_text.return_value = text
        p.extract_tables.return_value = tables if tables is not None else []
        return p

    def _make_mock_pdf(self, pages):
        mock_pdf = MagicMock()
        mock_pdf.pages = pages
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        return mock_pdf

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_multi_page_combines_transactions(self, mock_pdfplumber):
        page1 = self._make_page(
            "Chase Bank\nAccount ending in 1234",
            [[["01/15/2024", "WHOLE FOODS", "$89.47"]]]
        )
        page2 = self._make_page("", [[["01/16/2024", "SHELL OIL", "$45.00"]]])
        mock_pdfplumber.open.return_value = self._make_mock_pdf([page1, page2])

        result = parse_pdf("/fake/statement.pdf")
        self.assertTrue(result.success)
        self.assertEqual(len(result.transactions), 2)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_dedup_across_pages(self, mock_pdfplumber):
        # Same row on two different pages must collapse to one transaction
        row = ["01/15/2024", "WHOLE FOODS", "$89.47"]
        page1 = self._make_page("", [[row]])
        page2 = self._make_page("", [[row]])
        mock_pdfplumber.open.return_value = self._make_mock_pdf([page1, page2])

        result = parse_pdf("/fake/statement.pdf")
        self.assertEqual(len(result.transactions), 1)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_account_from_first_page_applied_to_all_transactions(self, mock_pdfplumber):
        # Page 1 has the account header but no transactions.
        # Page 2 has a transaction that must inherit page 1's detected account.
        page1 = self._make_page("Chase Bank\nAccount ending in 4231", [])
        page2 = self._make_page("", [[["01/16/2024", "STARBUCKS", "$5.50"]]])
        mock_pdfplumber.open.return_value = self._make_mock_pdf([page1, page2])

        result = parse_pdf("/fake/statement.pdf")
        self.assertEqual(len(result.transactions), 1)
        self.assertIn("Chase", result.transactions[0].account)
        self.assertIn("4231", result.transactions[0].account)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_row_count_equals_page_count(self, mock_pdfplumber):
        pages = [self._make_page() for _ in range(3)]
        mock_pdfplumber.open.return_value = self._make_mock_pdf(pages)

        result = parse_pdf("/fake/statement.pdf")
        self.assertEqual(result.row_count, 3)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_first_page_text_error_falls_back_to_account_label(self, mock_pdfplumber):
        # extract_text() raises on page 1 → header_text="" → account_label used instead.
        # Tables on the same page still succeed because they are processed first.
        page1 = MagicMock()
        page1.extract_text.side_effect = RuntimeError("unreadable")
        page1.extract_tables.return_value = [[["01/15/2024", "STORE", "$10.00"]]]
        mock_pdfplumber.open.return_value = self._make_mock_pdf([page1])

        result = parse_pdf("/fake/statement.pdf", account_label="My Bank ••9999")
        self.assertEqual(len(result.transactions), 1)
        self.assertEqual(result.transactions[0].account, "My Bank ••9999")

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_table_transactions_skip_text_fallback(self, mock_pdfplumber):
        # When tables yield at least one transaction the text fallback must not run.
        # A parseable line in the page text must NOT produce an extra transaction.
        page1 = self._make_page(
            "01/20/2024 TEXT ONLY MERCHANT $99.00",
            [[["01/15/2024", "TABLE MERCHANT", "$50.00"]]]
        )
        mock_pdfplumber.open.return_value = self._make_mock_pdf([page1])

        result = parse_pdf("/fake/statement.pdf")
        self.assertEqual(result.parser_used, "pdf_table")
        self.assertEqual(len(result.transactions), 1)
        self.assertNotIn("TEXT ONLY MERCHANT", result.transactions[0].merchant)

    def test_parser_used_is_pdf_auto_on_file_error(self):
        result = parse_pdf("/nonexistent/totally_fake_99999.pdf")
        self.assertEqual(result.parser_used, "pdf_auto")


# ══════════════════════════════════════════════════════════════════════════════
# _parse_short_date
# ══════════════════════════════════════════════════════════════════════════════

class TestParseShortDate(unittest.TestCase):

    def test_valid_month_day(self):
        self.assertEqual(_parse_short_date("Apr 13", 2026), date(2026, 4, 13))

    def test_leading_zero_day(self):
        self.assertEqual(_parse_short_date("Apr 01", 2026), date(2026, 4, 1))

    def test_whitespace_stripped(self):
        self.assertEqual(_parse_short_date("  Apr 13  ", 2026), date(2026, 4, 13))

    def test_year_is_applied(self):
        self.assertEqual(_parse_short_date("Jan 15", 2025).year, 2025)
        self.assertEqual(_parse_short_date("Jan 15", 2023).year, 2023)

    def test_invalid_month_returns_none(self):
        self.assertIsNone(_parse_short_date("Xyz 13", 2026))

    def test_invalid_day_returns_none(self):
        self.assertIsNone(_parse_short_date("Apr 99", 2026))


# ══════════════════════════════════════════════════════════════════════════════
# SHORT_DATE_PATTERN regex
# ══════════════════════════════════════════════════════════════════════════════

class TestShortDatePattern(unittest.TestCase):

    def test_matches_month_day(self):
        m = SHORT_DATE_PATTERN.search("Apr 13 MERCHANT $10.00")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "Apr 13")

    def test_does_not_match_full_date_with_comma_year(self):
        # negative lookahead must reject "Apr 13, 2026"
        self.assertIsNone(SHORT_DATE_PATTERN.search("Apr 13, 2026 MERCHANT $10.00"))

    def test_does_not_match_full_date_no_comma(self):
        # negative lookahead must reject "Apr 13 2026"
        self.assertIsNone(SHORT_DATE_PATTERN.search("Apr 13 2026 MERCHANT $10.00"))

    def test_matches_first_date_in_capital_one_line(self):
        # "Apr 13 Apr 13 MERCHANT $10.00" → first "Apr 13" matched
        m = SHORT_DATE_PATTERN.search("Apr 13 Apr 13 MERCHANT $10.00")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "Apr 13")

    def test_matches_leading_zero_day(self):
        m = SHORT_DATE_PATTERN.search("Apr 01 MERCHANT $5.00")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "Apr 01")

    def test_no_match_in_column_header(self):
        # "Trans Date Post Date Description Amount" contains no month abbreviation
        self.assertIsNone(SHORT_DATE_PATTERN.search("Trans Date Post Date Description Amount"))


# ══════════════════════════════════════════════════════════════════════════════
# _SUMMARY_LINE_RE  (tested indirectly via _parse_text_lines)
# ══════════════════════════════════════════════════════════════════════════════

class TestSummaryLineFilter(unittest.TestCase):

    def _parse(self, text):
        return _parse_text_lines(text, "test.pdf", "")

    def test_new_balance_filtered(self):
        self.assertEqual(len(self._parse("New Balance $1,090.80\n")), 0)

    def test_available_credit_with_embedded_date_filtered(self):
        # This specific line triggered the original false-positive bug
        self.assertEqual(
            len(self._parse("Available Credit (as of Apr 27, 2026) $28,909.20\n")), 0
        )

    def test_minimum_payment_filtered(self):
        self.assertEqual(len(self._parse("Minimum Payment Due $25.00\n")), 0)

    def test_total_fees_filtered(self):
        self.assertEqual(len(self._parse("Total Fees for This Period $0.00\n")), 0)

    def test_total_interest_filtered(self):
        self.assertEqual(len(self._parse("Total Interest Charged $0.00\n")), 0)

    def test_credit_limit_filtered(self):
        self.assertEqual(len(self._parse("Credit Limit $30,000.00\n")), 0)

    def test_case_insensitive(self):
        self.assertEqual(len(self._parse("new balance $1,000.00\n")), 0)

    def test_normal_merchant_not_filtered(self):
        self.assertEqual(len(self._parse("01/15/2024 WHOLE FOODS MARKET $89.47\n")), 1)


# ══════════════════════════════════════════════════════════════════════════════
# _parse_text_lines — year_hint parameter
# ══════════════════════════════════════════════════════════════════════════════

class TestParseTextLinesYearHint(unittest.TestCase):

    def test_short_date_parsed_with_year_hint(self):
        result = _parse_text_lines("Apr 13 MERCHANT $10.00\n", "test.pdf", "", year_hint=2026)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].date, date(2026, 4, 13))

    def test_short_date_not_parsed_without_year_hint(self):
        result = _parse_text_lines("Apr 13 MERCHANT $10.00\n", "test.pdf", "", year_hint=None)
        self.assertEqual(len(result), 0)

    def test_full_date_takes_precedence_over_year_hint(self):
        result = _parse_text_lines("01/15/2024 WHOLE FOODS $89.47\n", "test.pdf", "", year_hint=2026)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].date, date(2024, 1, 15))

    def test_short_dates_stripped_from_merchant(self):
        # "Apr 13 Apr 13 COFFEE SHOP $5.25" → merchant must not contain "Apr"
        result = _parse_text_lines("Apr 13 Apr 13 COFFEE SHOP $5.25\n", "test.pdf", "", year_hint=2026)
        self.assertEqual(len(result), 1)
        self.assertNotIn("Apr", result[0].merchant)
        self.assertIn("COFFEE SHOP", result[0].merchant)

    def test_year_applied_correctly(self):
        result = _parse_text_lines("Mar 28 WHOLE FOODS $89.47\n", "test.pdf", "", year_hint=2025)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].date, date(2025, 3, 28))


# ══════════════════════════════════════════════════════════════════════════════
# _parse_text_lines — Capital One-specific charge/payment format
# Capital One lists charges as positive "$X.XX" and payments as "- $X.XX"
# (space between the minus sign and the dollar sign).
# ══════════════════════════════════════════════════════════════════════════════

class TestParseTextLinesCapitalOneFormat(unittest.TestCase):

    def test_capital_one_charge_negated(self):
        # Positive "$X" with no prefix = charge in Capital One PDFs → must be negated
        result = _parse_text_lines("Apr 13 WHOLE FOODS $89.47\n", "test.pdf", "",
                                   year_hint=2026, invert_charges=True)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].amount, -89.47)

    def test_capital_one_credit_prefix_stays_positive(self):
        # Capital One payments use "- $X" (space between '-' and '$') → stays positive
        result = _parse_text_lines("Apr 13 AUTOPAY PYMT - $400.00\n", "test.pdf", "",
                                   year_hint=2026, invert_charges=True)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].amount, 400.00)

    def test_capital_one_mixed_batch(self):
        # One autopayment (credit prefix) + two charges on the same page
        text = (
            "Apr 13 AUTOPAY PYMT - $400.00\n"
            "Mar 28 WHOLE FOODS $89.47\n"
            "Apr 01 STARBUCKS $6.75\n"
        )
        result = _parse_text_lines(text, "test.pdf", "Capital One ••1234",
                                   year_hint=2026, invert_charges=True)
        self.assertEqual(len(result), 3)
        payment = next(t for t in result if "AUTOPAY" in t.merchant)
        charges  = [t for t in result if t.amount < 0]
        self.assertAlmostEqual(payment.amount, 400.00)
        self.assertEqual(len(charges), 2)


# ══════════════════════════════════════════════════════════════════════════════
# _parse_text_lines — Chase credit card-specific charge/payment format
# Chase lists charges as positive "X.XX" and payments as negative "-X.XX"
# (the hyphen is part of the number, no space before the digits).
# ══════════════════════════════════════════════════════════════════════════════

class TestParseTextLinesChaseFormat(unittest.TestCase):

    def test_chase_charge_inverted(self):
        # Chase charges are positive in the PDF — invert_charges turns them negative
        result = _parse_text_lines("03/23 WHOLE FOODS MARKET 89.47\n", "test.pdf", "",
                                   year_hint=2026, invert_charges=True)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].amount, -89.47)

    def test_chase_payment_negative_inverted(self):
        # Chase payments appear as "-350.00" in the PDF (no space after '-')
        # invert_charges treats them as charges and negates → +350.00
        result = _parse_text_lines("04/14 PAYMENT THANK YOU-MOBILE -350.00\n", "test.pdf", "",
                                   year_hint=2026, invert_charges=True)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].amount, 350.00)

    def test_chase_section_header_skipped(self):
        # "PAYMENTS AND OTHER CREDITS" has no date and no amount → must be skipped
        result = _parse_text_lines("PAYMENTS AND OTHER CREDITS\n", "test.pdf", "",
                                   year_hint=2026)
        self.assertEqual(len(result), 0)

    def test_no_invert_for_checking_account(self):
        # Checking account statements (invert_charges=False): sign is already correct
        result = _parse_text_lines("01/15/2024 STORE $89.47\n", "test.pdf", "",
                                   invert_charges=False)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].amount, 89.47)


# ══════════════════════════════════════════════════════════════════════════════
# parse_pdf — Capital One pipeline (mock-based)
# Tests specific to Capital One's format: month-name short dates, "- $X" credits,
# summary page filtering, and table rows with no merchant candidate.
# ══════════════════════════════════════════════════════════════════════════════

class TestParsePDFCapitalOneFormat(unittest.TestCase):

    def _make_page(self, text="", tables=None):
        p = MagicMock()
        p.extract_text.return_value = text
        p.extract_tables.return_value = tables if tables is not None else []
        return p

    def _make_mock_pdf(self, pages):
        mock_pdf = MagicMock()
        mock_pdf.pages = pages
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        return mock_pdf

    def _cap_one_header(self):
        return "Capital One Savor Credit Card\nAccount ending in 1234\nMar 29, 2026 - Apr 27, 2026"

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_capital_one_year_hint_from_header(self, mock_pdfplumber):
        """Full dates in the Capital One header are used to resolve month-name short dates."""
        txn_text = self._cap_one_header() + "\nApr 13 Apr 13 AUTOPAY PYMT - $400.00\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/cap_one.pdf")
        self.assertEqual(result.parser_used, "pdf_text_fallback")
        self.assertTrue(any(t.date.year == 2026 for t in result.transactions))

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_capital_one_charge_inverted(self, mock_pdfplumber):
        """Capital One positive-amount charge is negated (invert_charges activated by 'Credit Card')."""
        txn_text = self._cap_one_header() + "\nMar 28 WHOLE FOODS $89.47\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/cap_one.pdf")
        self.assertEqual(len(result.transactions), 1)
        self.assertAlmostEqual(result.transactions[0].amount, -89.47)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_capital_one_credit_prefix_payment(self, mock_pdfplumber):
        """Capital One '- $X' credit prefix keeps the amount positive."""
        txn_text = self._cap_one_header() + "\nApr 13 Apr 13 CAPITAL ONE AUTOPAY PYMT - $400.00\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/cap_one.pdf")
        self.assertEqual(len(result.transactions), 1)
        self.assertAlmostEqual(result.transactions[0].amount, 400.00)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_capital_one_summary_line_filtered(self, mock_pdfplumber):
        """'Available Credit' line with an embedded date and amount must not become a transaction."""
        txn_text = self._cap_one_header() + "\nAvailable Credit (as of Apr 27, 2026) $29,500.00\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/cap_one.pdf")
        self.assertEqual(len(result.transactions), 0)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_capital_one_summary_table_row_skipped(self, mock_pdfplumber):
        """Table row with date + amount but no merchant text returns None from _parse_table_row,
        blocking the false transaction that used to be generated from the summary page."""
        page = self._make_page(
            "Capital One Savor Credit Card\nAccount ending in 1234\nMar 29, 2026",
            tables=[[["May 22, 2026", "$1,090.80"]]]
        )
        mock_pdfplumber.open.return_value = self._make_mock_pdf([page])

        result = parse_pdf("/fake/cap_one.pdf")
        self.assertEqual(len(result.transactions), 0)


# ══════════════════════════════════════════════════════════════════════════════
# parse_pdf — Chase credit card pipeline (mock-based)
# Tests specific to Chase's format: MM/DD numeric short dates, negative payments,
# positive charges, and checking-account pass-through.
# ══════════════════════════════════════════════════════════════════════════════

class TestParsePDFChaseFormat(unittest.TestCase):

    def _make_page(self, text="", tables=None):
        p = MagicMock()
        p.extract_text.return_value = text
        p.extract_tables.return_value = tables if tables is not None else []
        return p

    def _make_mock_pdf(self, pages):
        mock_pdf = MagicMock()
        mock_pdf.pages = pages
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        return mock_pdf

    def _chase_cc_header(self):
        return "CHASE SOUTHWEST RAPID REWARDS CREDIT CARD\nAccount ending in 5678\nMar 22, 2026 - Apr 21, 2026"

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_chase_year_hint_from_header(self, mock_pdfplumber):
        """Full dates in the Chase header resolve MM/DD numeric short-date transactions."""
        txn_text = self._chase_cc_header() + "\n03/23 WHOLE FOODS 89.47\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/chase_cc.pdf")
        self.assertEqual(len(result.transactions), 1)
        self.assertEqual(result.transactions[0].date.year, 2026)
        self.assertEqual(result.transactions[0].date.month, 3)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_chase_charge_inverted(self, mock_pdfplumber):
        """Chase credit card positive charge is negated ('CREDIT CARD' in header activates inversion)."""
        txn_text = self._chase_cc_header() + "\n03/23 WHOLE FOODS 89.47\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/chase_cc.pdf")
        self.assertEqual(len(result.transactions), 1)
        self.assertAlmostEqual(result.transactions[0].amount, -89.47)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_chase_payment_negative_inverted(self, mock_pdfplumber):
        """Chase credit card payment appears as '-350.00' in the PDF; inversion yields +350.00."""
        txn_text = self._chase_cc_header() + "\n04/14 PAYMENT THANK YOU-MOBILE -350.00\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/chase_cc.pdf")
        self.assertEqual(len(result.transactions), 1)
        self.assertAlmostEqual(result.transactions[0].amount, 350.00)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_chase_checking_not_inverted(self, mock_pdfplumber):
        """Chase checking account header lacks 'credit card' → invert_charges=False → no inversion."""
        header = "Chase Total Checking\nAccount ending in 1234\nJan 1, 2024 - Jan 31, 2024"
        txn_text = header + "\n01/15/2024 WHOLE FOODS -$89.47\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/chase_checking.pdf")
        self.assertEqual(len(result.transactions), 1)
        self.assertAlmostEqual(result.transactions[0].amount, -89.47)


# ══════════════════════════════════════════════════════════════════════════════
# Integration — Capital One generated PDF (no mocks)
# ══════════════════════════════════════════════════════════════════════════════

class TestParsePDFCapitalOneIntegration(unittest.TestCase):
    """End-to-end tests using the Capital One sample PDF from the generators.
    No mocks — exercises the full pdfplumber I/O path."""

    _tmpdir = None
    _path = None

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp()
        cls._path = write_sample_pdf_files(cls._tmpdir)[0]  # capital_one_sample.pdf

    @classmethod
    def tearDownClass(cls):
        if cls._tmpdir:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _result(self):
        return parse_pdf(self._path)

    def test_transaction_count(self):
        self.assertEqual(len(self._result().transactions), CAPITAL_ONE_PDF_TRANSACTION_COUNT)

    def test_uses_text_fallback(self):
        self.assertEqual(self._result().parser_used, "pdf_text_fallback")

    def test_no_errors(self):
        self.assertEqual(self._result().errors, [])

    def test_account_detected(self):
        txns = self._result().transactions
        self.assertTrue(any("Capital One" in t.account for t in txns))
        self.assertTrue(any("1234" in t.account for t in txns))

    def test_autopayment_is_positive(self):
        # Autopayment uses the "- $X" credit prefix → stored as positive
        payments = [t for t in self._result().transactions if "AUTOPAY" in t.merchant.upper()]
        self.assertEqual(len(payments), 1)
        self.assertAlmostEqual(payments[0].amount, 400.00)
        self.assertEqual(payments[0].date, date(2026, 4, 13))

    def test_charges_are_negative(self):
        charges = sorted(t.amount for t in self._result().transactions if t.amount < 0)
        self.assertEqual(len(charges), 3)
        self.assertAlmostEqual(charges[0], -89.47)
        self.assertAlmostEqual(charges[1], -45.00)
        self.assertAlmostEqual(charges[2], -6.75)

    def test_dates_resolved_from_header_year(self):
        self.assertTrue(all(t.date.year == 2026 for t in self._result().transactions))

    def test_no_summary_phrase_in_merchants(self):
        merchants = " ".join(t.merchant.lower() for t in self._result().transactions)
        for phrase in ("available credit", "new balance", "minimum payment", "credit limit"):
            self.assertNotIn(phrase, merchants)


# ══════════════════════════════════════════════════════════════════════════════
# Integration — Chase credit card generated PDF (no mocks)
# ══════════════════════════════════════════════════════════════════════════════

class TestParsePDFChaseIntegration(unittest.TestCase):
    """End-to-end tests using the Chase credit card sample PDF from the generators.
    No mocks — exercises the full pdfplumber I/O path."""

    _tmpdir = None
    _path = None

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp()
        cls._path = write_sample_pdf_files(cls._tmpdir)[1]  # chase_statement_sample.pdf

    @classmethod
    def tearDownClass(cls):
        if cls._tmpdir:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _result(self):
        return parse_pdf(self._path)

    def test_transaction_count(self):
        self.assertEqual(len(self._result().transactions), CHASE_PDF_TRANSACTION_COUNT)

    def test_uses_text_fallback(self):
        self.assertEqual(self._result().parser_used, "pdf_text_fallback")

    def test_account_detected(self):
        txns = self._result().transactions
        self.assertTrue(any("Chase" in t.account for t in txns))
        self.assertTrue(any("5678" in t.account for t in txns))

    def test_payment_is_positive(self):
        # Chase PDFs store payments as negative; inversion makes them positive
        payments = [t for t in self._result().transactions if t.amount > 0]
        self.assertEqual(len(payments), 1)
        self.assertAlmostEqual(payments[0].amount, 350.00)

    def test_charges_are_negative(self):
        # Chase PDFs store charges as positive; inversion makes them negative
        charges = [t for t in self._result().transactions if t.amount < 0]
        self.assertEqual(len(charges), 3)

    def test_dates_resolved_from_header_year(self):
        # MM/DD dates use the year extracted from the statement header
        self.assertTrue(all(t.date.year == 2026 for t in self._result().transactions))


# ══════════════════════════════════════════════════════════════════════════════
# _parse_numeric_short_date
# ══════════════════════════════════════════════════════════════════════════════

class TestParseNumericShortDate(unittest.TestCase):

    def test_standard_mm_dd(self):
        self.assertEqual(_parse_numeric_short_date("04/14", 2026), date(2026, 4, 14))

    def test_single_digit_month(self):
        self.assertEqual(_parse_numeric_short_date("4/14", 2026), date(2026, 4, 14))

    def test_single_digit_day(self):
        self.assertEqual(_parse_numeric_short_date("04/2", 2026), date(2026, 4, 2))

    def test_year_applied(self):
        self.assertEqual(_parse_numeric_short_date("03/23", 2025).year, 2025)
        self.assertEqual(_parse_numeric_short_date("03/23", 2026).year, 2026)

    def test_invalid_month_returns_none(self):
        self.assertIsNone(_parse_numeric_short_date("13/14", 2026))

    def test_invalid_day_returns_none(self):
        self.assertIsNone(_parse_numeric_short_date("04/99", 2026))

    def test_whitespace_stripped(self):
        self.assertEqual(_parse_numeric_short_date("  04/14  ", 2026), date(2026, 4, 14))


# ══════════════════════════════════════════════════════════════════════════════
# NUMERIC_SHORT_DATE_PATTERN regex
# ══════════════════════════════════════════════════════════════════════════════

class TestNumericShortDatePattern(unittest.TestCase):

    def test_matches_mm_dd(self):
        m = NUMERIC_SHORT_DATE_PATTERN.search("04/14 MERCHANT $10.00")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "04/14")

    def test_does_not_match_full_date(self):
        self.assertIsNone(NUMERIC_SHORT_DATE_PATTERN.search("04/14/2026 MERCHANT $10.00"))

    def test_matches_single_digit_month(self):
        m = NUMERIC_SHORT_DATE_PATTERN.search("4/14 MERCHANT")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "4/14")

    def test_no_match_on_phone_number(self):
        self.assertIsNone(NUMERIC_SHORT_DATE_PATTERN.search("800-542-0820"))

    def test_no_match_in_column_header(self):
        self.assertIsNone(
            NUMERIC_SHORT_DATE_PATTERN.search("Transaction Merchant Name or Transaction Description $ Amount")
        )

    def test_matches_chase_format_line(self):
        m = NUMERIC_SHORT_DATE_PATTERN.search("04/14 Payment Thank You-Mobile -350.00")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "04/14")


# ══════════════════════════════════════════════════════════════════════════════
# _parse_text_lines — MM/DD date parsing (bank-agnostic)
# Covers only date resolution and merchant cleaning; inversion tests live in
# TestParseTextLinesChaseFormat.
# ══════════════════════════════════════════════════════════════════════════════

class TestParseTextLinesNumericShortDate(unittest.TestCase):

    def test_mm_dd_parsed_with_year_hint(self):
        result = _parse_text_lines("04/14 MERCHANT $10.00\n", "test.pdf", "", year_hint=2026)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].date, date(2026, 4, 14))

    def test_mm_dd_not_parsed_without_year_hint(self):
        result = _parse_text_lines("04/14 MERCHANT $10.00\n", "test.pdf", "", year_hint=None)
        self.assertEqual(len(result), 0)

    def test_mm_dd_removed_from_merchant(self):
        result = _parse_text_lines("04/14 WHOLE FOODS MARKET $89.47\n", "test.pdf", "",
                                   year_hint=2026)
        self.assertEqual(len(result), 1)
        self.assertNotIn("04/14", result[0].merchant)
        self.assertIn("WHOLE FOODS", result[0].merchant)

    def test_full_date_takes_precedence_over_numeric_short(self):
        result = _parse_text_lines("01/15/2024 STORE $89.47\n", "test.pdf", "", year_hint=2026)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].date, date(2024, 1, 15))


# ══════════════════════════════════════════════════════════════════════════════
# _parse_text_lines — BofA credit card-specific format
# BofA lists charges as positive and payments as negative (same convention as
# Chase credit cards), but the header says "Visa Signature" not "credit card".
# Each transaction line has TWO short dates: Trans Date and Post Date.
# ══════════════════════════════════════════════════════════════════════════════

class TestParseTextLinesBoFAFormat(unittest.TestCase):

    def test_bofa_charge_inverted(self):
        # BofA charges are positive in the PDF → negated by invert_charges
        result = _parse_text_lines("03/09 03/11 QUIK STOP FREMONT CA 57.92\n",
                                   "test.pdf", "", year_hint=2026, invert_charges=True)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].amount, -57.92)
        self.assertEqual(result[0].date.month, 3)
        self.assertEqual(result[0].date.day, 9)

    def test_bofa_payment_negative_inverted(self):
        # BofA payments appear as negative "-400.00" → inversion yields +400.00
        result = _parse_text_lines(
            "03/11 03/13 ONLINE/MOBILE RECURRING FROM CHK -400.00\n",
            "test.pdf", "", year_hint=2026, invert_charges=True
        )
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].amount, 400.00)

    def test_bofa_both_short_dates_stripped_from_merchant(self):
        # BofA format has two MM/DD columns — both must be removed from merchant text
        result = _parse_text_lines("03/09 03/11 QUIK STOP FREMONT CA 57.92\n",
                                   "test.pdf", "", year_hint=2026, invert_charges=True)
        self.assertEqual(len(result), 1)
        self.assertNotIn("03/09", result[0].merchant)
        self.assertNotIn("03/11", result[0].merchant)
        self.assertIn("QUIK STOP", result[0].merchant)

    def test_bofa_trans_date_used_for_date(self):
        # The FIRST MM/DD (Trans Date) is parsed as the transaction date
        result = _parse_text_lines("03/09 03/11 QUIK STOP 57.92\n",
                                   "test.pdf", "", year_hint=2026, invert_charges=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].date, date(2026, 3, 9))

    def test_bofa_em_dash_credit_parsed(self):
        # BofA uses em-dash (—) for negative credit amounts.
        # Without the fix, AMOUNT_PATTERN misses —400.00 and the line is skipped.
        result = _parse_text_lines(
            "03/11 03/13 ONLINE/MOBILE RECURRING FROM CHK —400.00\n",
            "test.pdf", "", year_hint=2026, invert_charges=True
        )
        self.assertEqual(len(result), 1, "em-dash credit must not be skipped")
        self.assertAlmostEqual(result[0].amount, 400.00)

    def test_bofa_en_dash_credit_parsed(self):
        result = _parse_text_lines(
            "03/28 03/30 AMAZON RETURN –115.75\n",
            "test.pdf", "", year_hint=2026, invert_charges=True
        )
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].amount, 115.75)

    def test_bofa_interest_charged_line_filtered(self):
        # "INTEREST CHARGED ON PURCHASES" was not matched by the old regex
        # (interest charge\b fails because CHARGED has a 'D' after CHARGE).
        result = _parse_text_lines(
            "04/10 04/10 INTEREST CHARGED ON PURCHASES 0.00\n",
            "test.pdf", "", year_hint=2026, invert_charges=True
        )
        self.assertEqual(len(result), 0, "interest-charged summary line must be filtered")


# ══════════════════════════════════════════════════════════════════════════════
# parse_pdf — BofA pipeline (mock-based)
# Tests specific to BofA's format: "Visa Signature" header triggers invert_charges,
# unmasked full account number, MM/DD two-date transaction lines.
# ══════════════════════════════════════════════════════════════════════════════

class TestParsePDFBoFAFormat(unittest.TestCase):

    def _make_page(self, text="", tables=None):
        p = MagicMock()
        p.extract_text.return_value = text
        p.extract_tables.return_value = tables if tables is not None else []
        return p

    def _make_mock_pdf(self, pages):
        mock_pdf = MagicMock()
        mock_pdf.pages = pages
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        return mock_pdf

    def _bofa_header(self):
        return (
            "Bank of America\nVisa Signature\n"
            "Account# XXXX XXXX XXXX 9012\n"
            "March 11 - April 10, 2026\n"
            "Payment Due Date 05/07/2026"
        )

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_bofa_visa_activates_invert_charges(self, mock_pdfplumber):
        """'Visa Signature' in header (not 'credit card') must still activate invert_charges."""
        txn_text = self._bofa_header() + "\n03/09 03/11 QUIK STOP 57.92\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/bofa.pdf")
        self.assertEqual(len(result.transactions), 1)
        self.assertAlmostEqual(result.transactions[0].amount, -57.92)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_bofa_payment_inverted(self, mock_pdfplumber):
        """BofA payment '-400.00' in PDF is inverted to +400.00."""
        txn_text = self._bofa_header() + "\n03/11 03/13 ONLINE/MOBILE RECURRING FROM CHK -400.00\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/bofa.pdf")
        self.assertEqual(len(result.transactions), 1)
        self.assertAlmostEqual(result.transactions[0].amount, 400.00)

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_bofa_account_detected(self, mock_pdfplumber):
        """BofA with masked account number 'XXXX XXXX XXXX 9012' → 'BofA ••9012'."""
        txn_text = self._bofa_header() + "\n03/09 03/11 QUIK STOP 57.92\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/bofa.pdf")
        self.assertTrue(any("BofA" in t.account for t in result.transactions))
        self.assertTrue(any("9012" in t.account for t in result.transactions))

    @patch("finn_tracker.parsers.pdf_parser.pdfplumber")
    def test_bofa_year_hint_from_payment_due_date(self, mock_pdfplumber):
        """Full date '05/07/2026' in header provides year_hint to resolve MM/DD transactions."""
        txn_text = self._bofa_header() + "\n03/09 03/11 QUIK STOP 57.92\n"
        mock_pdfplumber.open.return_value = self._make_mock_pdf([self._make_page(txn_text)])

        result = parse_pdf("/fake/bofa.pdf")
        self.assertEqual(len(result.transactions), 1)
        self.assertEqual(result.transactions[0].date.year, 2026)
        self.assertEqual(result.transactions[0].date.month, 3)


# ══════════════════════════════════════════════════════════════════════════════
# Integration — BofA generated PDF (no mocks)
# ══════════════════════════════════════════════════════════════════════════════

class TestParsePDFBoFAIntegration(unittest.TestCase):
    """End-to-end tests using the BofA Visa sample PDF from the generators.
    No mocks — exercises the full pdfplumber I/O path."""

    _tmpdir = None
    _path = None

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp()
        cls._path = write_sample_pdf_files(cls._tmpdir)[2]  # bofa_statement_sample.pdf

    @classmethod
    def tearDownClass(cls):
        if cls._tmpdir:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _result(self):
        return parse_pdf(self._path)

    def test_transaction_count(self):
        self.assertEqual(len(self._result().transactions), BOFA_PDF_TRANSACTION_COUNT)

    def test_uses_text_fallback(self):
        self.assertEqual(self._result().parser_used, "pdf_text_fallback")

    def test_no_errors(self):
        self.assertEqual(self._result().errors, [])

    def test_account_detected(self):
        txns = self._result().transactions
        self.assertTrue(any("BofA" in t.account for t in txns))
        self.assertTrue(any("9012" in t.account for t in txns))

    def test_payment_is_positive(self):
        # BofA PDF shows payment as negative; inversion makes it positive
        payments = [t for t in self._result().transactions if t.amount > 0]
        self.assertEqual(len(payments), 1)
        self.assertAlmostEqual(payments[0].amount, 400.00)
        self.assertEqual(payments[0].date, date(2026, 3, 11))

    def test_charges_are_negative(self):
        # BofA PDF shows charges as positive; inversion makes them negative
        charges = [t for t in self._result().transactions if t.amount < 0]
        self.assertEqual(len(charges), 3)
        amounts = sorted(t.amount for t in charges)
        self.assertAlmostEqual(amounts[0], -89.47)
        self.assertAlmostEqual(amounts[1], -57.92)
        self.assertAlmostEqual(amounts[2], -52.61)

    def test_dates_resolved_from_header_year(self):
        self.assertTrue(all(t.date.year == 2026 for t in self._result().transactions))

    def test_visa_header_activates_invert_charges(self):
        # The generator uses "Visa Signature" (not "credit card") — verify inversion happened
        charges = [t for t in self._result().transactions if t.amount < 0]
        self.assertEqual(len(charges), 3)  # would be 0 if invert_charges=False


class TestCollectDates(unittest.TestCase):

    def test_single_full_date(self):
        hits = _collect_dates("01/15/2024 MERCHANT $50.00")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], date(2024, 1, 15))

    def test_two_short_dates_returns_both_in_order(self):
        hits = _collect_dates("04/13 04/14 MERCHANT $50.00", year_hint=2026)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0][1], date(2026, 4, 13))
        self.assertEqual(hits[1][1], date(2026, 4, 14))

    def test_two_month_name_short_dates(self):
        hits = _collect_dates("Apr 13 Apr 14 MERCHANT $50.00", year_hint=2026)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0][1], date(2026, 4, 13))
        self.assertEqual(hits[1][1], date(2026, 4, 14))

    def test_no_dates_returns_empty(self):
        hits = _collect_dates("MERCHANT $50.00")
        self.assertEqual(hits, [])

    def test_deduplicates_overlapping_match(self):
        # A full MM/DD/YYYY should not also produce a MM/DD short-date hit nearby
        hits = _collect_dates("01/15/2024 MERCHANT", year_hint=2024)
        self.assertEqual(len(hits), 1)


class TestFindPostDateCol(unittest.TestCase):

    def test_detects_post_date_column(self):
        table = [
            ["Trans Date", "Post Date", "Description", "Amount"],
            ["04/13", "04/14", "MERCHANT", "$50.00"],
        ]
        self.assertEqual(_find_post_date_col(table), 1)

    def test_detects_posting_date_variant(self):
        table = [
            ["Date", "Posting Date", "Description", "Amount"],
            ["04/13", "04/14", "MERCHANT", "$50.00"],
        ]
        self.assertEqual(_find_post_date_col(table), 1)

    def test_detects_posted_date_variant(self):
        table = [
            ["Transaction Date", "Posted Date", "Payee", "Amount"],
            ["04/13", "04/14", "MERCHANT", "$50.00"],
        ]
        self.assertEqual(_find_post_date_col(table), 1)

    def test_returns_minus_one_when_no_post_date_col(self):
        table = [
            ["Date", "Description", "Amount"],
            ["04/14", "MERCHANT", "$50.00"],
        ]
        self.assertEqual(_find_post_date_col(table), -1)

    def test_skips_data_rows(self):
        # Table with no header — all rows have actual dates
        table = [
            ["04/13", "04/14", "MERCHANT", "$50.00"],
        ]
        self.assertEqual(_find_post_date_col(table), -1)


class TestDetectPostDatePosition(unittest.TestCase):

    def test_trans_before_post_returns_1(self):
        text = "Trans Date Post Date Description Amount\n04/13 04/14 MERCHANT $50.00"
        self.assertEqual(_detect_post_date_position(text), 1)

    def test_post_before_trans_returns_0(self):
        text = "Post Date Trans Date Description Amount\n04/14 04/13 MERCHANT $50.00"
        self.assertEqual(_detect_post_date_position(text), 0)

    def test_only_post_date_returns_0(self):
        text = "Post Date Description Amount\n04/14 MERCHANT $50.00"
        self.assertEqual(_detect_post_date_position(text), 0)

    def test_no_header_returns_minus_one(self):
        text = "04/14 MERCHANT $50.00\n04/15 OTHER $30.00"
        self.assertEqual(_detect_post_date_position(text), -1)

    def test_transaction_date_variant(self):
        text = "Transaction Date Post Date Description Amount"
        self.assertEqual(_detect_post_date_position(text), 1)

    def test_bofa_split_header_returns_1(self):
        # BofA prints column labels on one line and "Date Date" on the next
        text = (
            "Transaction Posting Reference Account\n"
            "Date Date Description Number Number Amount Total\n"
            "03/09 03/11 SOME MERCHANT 57.92"
        )
        self.assertEqual(_detect_post_date_position(text), 1)

    def test_split_header_body_text_posting_date_does_not_mislead(self):
        # Body text on page 2 of BofA PDFs contains "posting date" in fine print.
        # The split-header pass must fire before the single-line pass misreads it.
        text = (
            "interest accrues from the transaction date or posting date within the billing cycle.\n"
            "Transaction Posting Reference Account\n"
            "Date Date Description Number Number Amount Total\n"
        )
        self.assertEqual(_detect_post_date_position(text), 1)


class TestParseTextLinesPostDate(unittest.TestCase):

    def test_post_date_position_1_picks_second_date(self):
        text = "Trans Date Post Date Description Amount\n04/13 04/15 COFFEE SHOP $5.00"
        txns = _parse_text_lines(text, "stmt.pdf", "Chase", year_hint=2026,
                                  invert_charges=False, post_date_position=1)
        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0].date, date(2026, 4, 15))

    def test_post_date_position_minus_one_picks_first_date(self):
        text = "Trans Date Post Date Description Amount\n04/13 04/15 COFFEE SHOP $5.00"
        txns = _parse_text_lines(text, "stmt.pdf", "Chase", year_hint=2026,
                                  invert_charges=False, post_date_position=-1)
        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0].date, date(2026, 4, 13))

    def test_post_date_position_beyond_available_falls_back_to_first(self):
        # Only one date on the line — position=1 is out of range, should use index 0
        text = "04/15 COFFEE SHOP $5.00"
        txns = _parse_text_lines(text, "stmt.pdf", "Chase", year_hint=2026,
                                  invert_charges=False, post_date_position=1)
        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0].date, date(2026, 4, 15))


class TestParseTableRowPostDateCol(unittest.TestCase):

    def test_uses_post_date_col_when_set(self):
        row = ["04/13/2024", "04/15/2024", "COFFEE SHOP", "$5.00"]
        t = _parse_table_row(row, "stmt.pdf", "Chase ••1234", post_date_col=1)
        self.assertIsNotNone(t)
        self.assertEqual(t.date, date(2024, 4, 15))

    def test_falls_back_to_first_date_when_col_minus_one(self):
        row = ["04/13/2024", "04/15/2024", "COFFEE SHOP", "$5.00"]
        t = _parse_table_row(row, "stmt.pdf", "Chase ••1234", post_date_col=-1)
        self.assertIsNotNone(t)
        self.assertEqual(t.date, date(2024, 4, 13))

    def test_falls_back_when_post_date_col_out_of_range(self):
        row = ["04/13/2024", "COFFEE SHOP", "$5.00"]
        t = _parse_table_row(row, "stmt.pdf", "Chase ••1234", post_date_col=5)
        self.assertIsNotNone(t)
        self.assertEqual(t.date, date(2024, 4, 13))


if __name__ == "__main__":
    unittest.main()
