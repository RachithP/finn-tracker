"""
PDF Parser — extracts transactions from bank/credit card statement PDFs.
Uses pdfplumber for table + text extraction. Data stays local.
"""
import re
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Tuple

import pdfplumber

from models import Transaction, ParseResult, mask_sensitive


DATE_PATTERNS = [
    r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b",       # 01/15/2024 or 1/15/24
    r"\b(\d{4}-\d{2}-\d{2})\b",               # 2024-01-15
    r"\b([A-Z][a-z]{2}\.?\s+\d{1,2},?\s+\d{4})\b",  # Jan 15, 2024 / Jan. 15 2024
    r"\b(\d{1,2}-[A-Z][a-z]{2}-\d{2,4})\b",  # 15-Jan-24
]
DATE_FORMATS = [
    "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d",
    "%b %d, %Y", "%b. %d, %Y", "%b %d %Y",
    "%d-%b-%Y", "%d-%b-%y",
]
AMOUNT_PATTERN = re.compile(r"-?\$?\s*[\d,]+\.\d{2}")


def _detect_account_from_text(text: str) -> str:
    """Identify the bank and last-4 of the account from PDF header text.
    Returns a label like 'Chase ••1234', or '' if nothing matches."""
    if not text:
        return ""
    low = text.lower()
    if "capital one" in low:
        bank = "Capital One"
    elif "bank of america" in low or "bankofamerica" in low:
        bank = "BofA"
    elif "chase" in low or "jpmorgan" in low:
        bank = "Chase"
    else:
        bank = ""

    last4 = ""
    patterns = [
        r'ending\s+in\s*[#:]?\s*(\d{4})\b',
        r'account\s*(?:number|#|no\.?)\s*[:\-]?\s*[\*xX\d\s\-]*?(\d{4})\b',
        r'card\s*(?:number|#|no\.?)\s*[:\-]?\s*[\*xX\d\s\-]*?(\d{4})\b',
        r'[\*xX]{2,}\s*[\-\s]?(\d{4})\b',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            last4 = m.group(1)
            break

    if bank and last4:
        return f"{bank} ••{last4}"
    if bank:
        return bank
    return ""


def _parse_date(raw: str) -> Optional[date]:
    raw = raw.strip().replace(",", "")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(raw: str) -> Optional[float]:
    s = raw.strip().replace(",", "").replace("$", "").replace(" ", "")
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        val = float(s)
        if val != val:  # NaN check
            return None
        return -val if negative else val
    except ValueError:
        return None


def _extract_date_from_cell(cell: str) -> Optional[date]:
    if not cell:
        return None
    for pattern in DATE_PATTERNS:
        m = re.search(pattern, cell)
        if m:
            parsed = _parse_date(m.group(1))
            if parsed:
                return parsed
    return None


def _is_transaction_row(row: List[str]) -> bool:
    """Heuristic: a row is a transaction if it has a date and a dollar amount."""
    has_date = any(_extract_date_from_cell(str(c or "")) for c in row)
    has_amount = any(AMOUNT_PATTERN.search(str(c or "")) for c in row)
    return has_date and has_amount


def _parse_table_row(row: List[str], source_file: str, account: str) -> Optional[Transaction]:
    """Try to extract a Transaction from a table row."""
    cells = [str(c or "").strip() for c in row]

    txn_date = None
    merchant = ""
    amount = None

    for cell in cells:
        if txn_date is None:
            txn_date = _extract_date_from_cell(cell)

    # Amount: take the last cell that looks like a dollar amount
    for cell in reversed(cells):
        m = AMOUNT_PATTERN.search(cell)
        if m:
            amount = _parse_amount(m.group(0))
            break

    # Merchant: longest non-date, non-amount cell
    candidates = []
    for cell in cells:
        if not cell:
            continue
        if AMOUNT_PATTERN.search(cell):
            continue
        if _extract_date_from_cell(cell) and len(cell) < 15:
            continue
        candidates.append(cell)

    merchant = max(candidates, key=len, default="Unknown")

    if txn_date and amount is not None:
        return Transaction(
            date=txn_date,
            merchant=mask_sensitive(merchant),
            amount=amount,
            category="Uncategorized",
            account=account,
            source_file=source_file,
            raw_description=merchant,
        )
    return None


def _parse_text_lines(text: str, source_file: str, account: str) -> List[Transaction]:
    """
    Fallback: parse transactions from raw text when no tables found.
    Looks for lines containing a date and a dollar amount.
    """
    transactions = []
    lines = text.splitlines()

    for line in lines:
        line = line.strip()
        if len(line) < 10:
            continue

        txn_date = None
        for pattern in DATE_PATTERNS:
            m = re.search(pattern, line)
            if m:
                txn_date = _parse_date(m.group(1))
                if txn_date:
                    break

        if not txn_date:
            continue

        amount_matches = AMOUNT_PATTERN.findall(line)
        if not amount_matches:
            continue

        amount = _parse_amount(amount_matches[-1])
        if amount is None:
            continue

        # Merchant = everything between date and amount
        clean = re.sub(r"-?\$?\s*[\d,]+\.\d{2}", "", line)
        for pattern in DATE_PATTERNS:
            clean = re.sub(pattern, "", clean)
        merchant = re.sub(r"\s+", " ", clean).strip(" |-_")
        merchant = merchant[:80] if merchant else "Unknown"

        transactions.append(Transaction(
            date=txn_date,
            merchant=mask_sensitive(merchant),
            amount=amount,
            category="Uncategorized",
            account=account,
            source_file=source_file,
            raw_description=merchant,
        ))

    return transactions


def parse_pdf(file_path: str, account_label: str = "") -> ParseResult:
    """
    Parse a bank/credit card statement PDF.

    Strategy:
    1. Extract tables with pdfplumber (best for structured statements)
    2. Fall back to raw text line parsing if no tables found

    Args:
        file_path: Absolute local path to the PDF.
        account_label: Optional masked label e.g. 'Chase ••4231'.

    Returns:
        ParseResult with normalized Transaction objects.
    """
    result = ParseResult(source_file=Path(file_path).name)
    transactions = []
    errors = []

    try:
        with pdfplumber.open(file_path) as pdf:
            result.row_count = len(pdf.pages)
            all_text = ""

            # Pre-scan first page for account header so we can label txns.
            header_text = ""
            if pdf.pages:
                try:
                    header_text = pdf.pages[0].extract_text() or ""
                except Exception:
                    header_text = ""
            detected_account = _detect_account_from_text(header_text)
            account_used = detected_account or account_label

            for page_num, page in enumerate(pdf.pages, 1):
                try:
                    # --- Strategy 1: table extraction ---
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue
                        for row in table:
                            if not row or not _is_transaction_row(row):
                                continue
                            t = _parse_table_row(row, result.source_file, account_used)
                            if t:
                                transactions.append(t)

                    # Accumulate text for fallback
                    page_text = page.extract_text() or ""
                    all_text += page_text + "\n"

                except Exception as e:
                    errors.append(f"Page {page_num}: {str(e)}")

            # --- Strategy 2: text fallback if tables yielded nothing ---
            if not transactions and all_text.strip():
                transactions = _parse_text_lines(all_text, result.source_file, account_used)
                result.parser_used = "pdf_text_fallback"
            else:
                result.parser_used = "pdf_table"

            # Deduplicate by (date, merchant, amount)
            seen = set()
            unique = []
            for t in transactions:
                key = (t.date, t.merchant, t.amount)
                if key not in seen:
                    seen.add(key)
                    unique.append(t)

            result.transactions = unique
            result.errors = errors

    except Exception as e:
        result.errors.append(f"File-level error: {str(e)}")
        result.parser_used = "pdf_auto"

    return result
