"""
Ingestion Engine — routes files to the correct parser.
Supports: .csv, .pdf
All processing is local. No data sent to any server.
"""
import os
from pathlib import Path

from finn_tracker.models import ParseResult
from finn_tracker.parsers.csv_parser import parse_csv
from finn_tracker.parsers.pdf_parser import parse_pdf


SUPPORTED_EXTENSIONS = {".csv", ".pdf"}


def ingest_file(file_path: str, account_label: str = "") -> ParseResult:
    """
    Ingest a single file (CSV or PDF) and return normalized transactions.

    Args:
        file_path: Absolute local path to the file.
        account_label: Optional display label (already masked by caller).

    Returns:
        ParseResult
    """
    path = Path(file_path)

    if not path.exists():
        r = ParseResult(source_file=path.name)
        r.errors.append(
            f"Could not read {path.name}. File not found. "
            f"Make sure it's exported directly from your bank (not moved or renamed)."
        )
        return r

    ext = path.suffix.lower()

    if ext == ".csv":
        result = parse_csv(file_path, account_label)
        if not result.transactions and result.errors:
            result.errors = [
                f"Could not read {path.name}. "
                f"Make sure it's exported directly from your bank (not modified in Excel). "
                f"Supported: Chase, BofA, generic CSV."
            ] + result.errors[1:]
        return result
    elif ext == ".pdf":
        result = parse_pdf(file_path, account_label)
        if not result.transactions and result.errors:
            result.errors = [
                f"Could not read {path.name}. "
                f"Make sure it's exported directly from your bank (not modified in Excel). "
                f"Supported: Capital One, Chase, BofA, and generic table-based PDFs."
            ] + result.errors[1:]
        return result
    else:
        r = ParseResult(source_file=path.name)
        r.errors.append(
            f"Could not read {path.name}. Unsupported file type '{ext}'. "
            f"Supported formats: CSV and PDF exports from your bank."
        )
        return r
