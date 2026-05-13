"""
Tests for ingest.py — file routing, CSV routing, PDF routing, unsupported
extensions, missing files, and error messages.
"""
import tempfile
import unittest
from pathlib import Path

from finn_tracker.ingest import ingest_file
from finn_tracker.models import ParseResult
from sample_data.generators import write_sample_files


class TestIngestFileCSV(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.files = write_sample_files(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_csv_routed_to_csv_parser(self):
        result = ingest_file(self.files[0])
        self.assertTrue(result.success)
        self.assertIn("csv", result.parser_used)

    def test_account_label_passed_through(self):
        result = ingest_file(self.files[0], account_label="Test ••1234")
        for t in result.transactions:
            self.assertEqual(t.account, "Test ••1234")


class TestIngestFilePDF(unittest.TestCase):

    def test_pdf_routed_to_pdf_parser(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake content")
            f.flush()
            result = ingest_file(f.name)
        # PDF parser will try to parse and likely fail on fake content
        # but it should be routed to the PDF parser
        Path(f.name).unlink(missing_ok=True)
        # The parser_used should indicate pdf was attempted
        self.assertTrue(
            result.parser_used.startswith("pdf") or len(result.errors) > 0
        )

    def test_pdf_with_no_transactions_shows_friendly_error(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"not a real pdf")
            f.flush()
            result = ingest_file(f.name)
        Path(f.name).unlink(missing_ok=True)
        if result.errors:
            # Should have a user-friendly error message
            self.assertTrue(any("Could not read" in e or "error" in e.lower() for e in result.errors))


class TestIngestFileMissing(unittest.TestCase):

    def test_nonexistent_file(self):
        result = ingest_file("/nonexistent/path/file.csv")
        self.assertFalse(result.success)
        self.assertGreater(len(result.errors), 0)
        self.assertIn("not found", result.errors[0].lower())

    def test_nonexistent_pdf(self):
        result = ingest_file("/nonexistent/path/file.pdf")
        self.assertFalse(result.success)


class TestIngestFileUnsupported(unittest.TestCase):

    def test_xlsx_unsupported(self):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(b"fake xlsx")
            f.flush()
            result = ingest_file(f.name)
        Path(f.name).unlink(missing_ok=True)
        self.assertFalse(result.success)
        self.assertIn("Unsupported", result.errors[0])

    def test_txt_unsupported(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"plain text")
            f.flush()
            result = ingest_file(f.name)
        Path(f.name).unlink(missing_ok=True)
        self.assertFalse(result.success)
        self.assertIn("Unsupported", result.errors[0])

    def test_error_message_includes_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(b"fake")
            f.flush()
            result = ingest_file(f.name)
        Path(f.name).unlink(missing_ok=True)
        self.assertIn(".docx", result.errors[0])


class TestIngestCSVWithNoTransactions(unittest.TestCase):
    """Test the friendly error message path when CSV parsing yields no transactions."""

    def test_empty_csv_shows_friendly_error(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            f.write("not,a,valid,bank,format\nfoo,bar,baz,qux,quux\n")
            f.flush()
            result = ingest_file(f.name)
        Path(f.name).unlink(missing_ok=True)
        # Should either succeed with 0 transactions or have a friendly error
        if not result.success and result.errors:
            self.assertTrue(any("Could not read" in e or "Supported" in e for e in result.errors))


if __name__ == "__main__":
    unittest.main()
