"""
Ingestion Engine — routes files to the correct parser.
Supports: .csv, .pdf
All processing is local. No data sent to any server.
"""
import os
from pathlib import Path
from typing import List

from finn_tracker.models import ParseResult, Transaction
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
                f"Supported: Chase, BofA, generic PDF."
            ] + result.errors[1:]
        return result
    else:
        r = ParseResult(source_file=path.name)
        r.errors.append(
            f"Could not read {path.name}. Unsupported file type '{ext}'. "
            f"Supported formats: CSV and PDF exports from your bank."
        )
        return r


def ingest_files(file_paths: List[str], account_label: str = "") -> List[ParseResult]:
    """Ingest multiple files and return a list of ParseResults."""
    return [ingest_file(fp, account_label) for fp in file_paths]


def merge_results(results: List[ParseResult]) -> List[Transaction]:
    """Flatten multiple ParseResults into a single sorted transaction list."""
    all_txns = []
    for r in results:
        all_txns.extend(r.transactions)
    return sorted(all_txns, key=lambda t: t.date, reverse=True)


def print_summary(results: List[ParseResult]):
    """Print a privacy-safe ingestion summary to stdout."""
    print("\n" + "=" * 60)
    print("  INGESTION SUMMARY")
    print("=" * 60)
    for r in results:
        s = r.summary
        print(f"\n  File        : {r.source_file}")
        print(f"  Parser      : {r.parser_used}")
        print(f"  Transactions: {s['count']}")
        print(f"  Expenses    : ${s['total_expenses']:,.2f}")
        print(f"  Income      : ${s['total_income']:,.2f}")
        print(f"  Net         : ${s['net']:,.2f}")
        if s["errors"]:
            print(f"  Warnings    : {len(s['errors'])} row-level issues")
    print("=" * 60 + "\n")
