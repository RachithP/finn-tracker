"""
PDF Parser — extracts transactions from bank/credit card statement PDFs.
Uses pdfplumber for table + text extraction. Data stays local.
"""
import re
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Tuple

import pdfplumber

from finn_tracker.models import Transaction, ParseResult, mask_sensitive, parse_amount


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
# Matches dollar amounts including Unicode minus variants (em-dash, en-dash, U+2212)
# that BofA and some other banks use for negative/credit amounts in their PDFs.
AMOUNT_PATTERN = re.compile(r"[-−–—]?\$?\s*[\d,]+\.\d{2}")
# Matches short dates like "Apr 13" (month + day, no year) that do NOT have a 4-digit
# year immediately following them (which would already be caught by DATE_PATTERNS).
SHORT_DATE_PATTERN = re.compile(r"\b([A-Z][a-z]{2}\s+\d{1,2})\b(?![\s,.-]*\d{4})")
# Matches numeric short dates like "04/14" (MM/DD without year).
# The negative lookahead ensures we never match the MM/DD part of a full MM/DD/YYYY.
NUMERIC_SHORT_DATE_PATTERN = re.compile(r"\b(\d{1,2}/\d{1,2})\b(?!/)")
# Lines containing these phrases are statement summaries, not transaction rows.
_SUMMARY_LINE_RE = re.compile(
    r"\b(?:available credit|credit limit|new balance|minimum payment|previous balance|"
    r"total fees|total interest|interest charge[ds]?|cash advance limit|rewards balance|"
    r"payment due|due date|closing date|statement closing|account summary)\b",
    re.IGNORECASE,
)
# Column header keywords for date column detection.
_POST_DATE_RE = re.compile(r"\bpost(?:ing|ed)?\s*date\b", re.IGNORECASE)
_TRANS_DATE_RE = re.compile(r"\btrans(?:action)?\.?\s*date\b", re.IGNORECASE)


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
        # Greedy quantifier so the match backtracks to the LAST 4-digit group.
        # This handles both masked (XXXX-XXXX-XXXX-NNNN) and unmasked
        # (NNNN NNNN NNNN NNNN) account number formats correctly.
        r'account\s*(?:number|#|no\.?)\s*[:\-]?\s*[\*xX\d\s\-]*(\d{4})\b',
        r'card\s*(?:number|#|no\.?)\s*[:\-]?\s*[\*xX\d\s\-]*(\d{4})\b',
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


def _parse_short_date(raw: str, year: int) -> Optional[date]:
    """Parse a month-name short date like 'Apr 13' using a supplied year."""
    try:
        return datetime.strptime(f"{raw.strip()} {year}", "%b %d %Y").date()
    except ValueError:
        return None


def _parse_numeric_short_date(raw: str, year: int) -> Optional[date]:
    """Parse a numeric short date like '04/14' (MM/DD) using a supplied year."""
    try:
        return datetime.strptime(f"{raw.strip()}/{year}", "%m/%d/%Y").date()
    except ValueError:
        return None


def _collect_dates(line: str, year_hint: Optional[int] = None) -> List[Tuple[int, date]]:
    """Return all (start_pos, date) pairs found in line, sorted left-to-right.
    Deduplicates overlapping matches (e.g. short date inside a full date)."""
    hits: List[Tuple[int, date]] = []

    def _add(start: int, d: date) -> None:
        if d and not any(abs(start - p) < 4 for p, _ in hits):
            hits.append((start, d))

    for pattern in DATE_PATTERNS:
        for m in re.finditer(pattern, line):
            _add(m.start(), _parse_date(m.group(1)))
    if year_hint:
        for m in SHORT_DATE_PATTERN.finditer(line):
            _add(m.start(), _parse_short_date(m.group(1), year_hint))
        for m in NUMERIC_SHORT_DATE_PATTERN.finditer(line):
            _add(m.start(), _parse_numeric_short_date(m.group(1), year_hint))

    hits.sort(key=lambda x: x[0])
    return hits


def _find_post_date_col(table: List[List]) -> int:
    """Scan a table's rows for a header row containing a post-date keyword.
    Returns the column index of that header cell, or -1 if not found.
    A header row is identified by having no actual date values in its cells."""
    for row in table:
        cells = [str(c or "").strip() for c in row]
        if any(_extract_date_from_cell(c) for c in cells):
            continue  # data row — skip
        for i, cell in enumerate(cells):
            if _POST_DATE_RE.search(cell):
                return i
    return -1


def _detect_post_date_position(text: str) -> int:
    """Scan text for column headers that identify which date position is the post date.
    Returns the 0-based index of the post date among all dates on a transaction line
    (0 = leftmost, 1 = second from left), or -1 if no such header is found.

    Two passes are used:
    1. Split-header pass: handles banks (e.g. BofA) that print column headers across
       two lines, e.g. "Transaction  Posting  ..." on one line and "Date  Date  ..."
       on the next.  Checked first so body-text mentions of "posting date" in fine
       print do not trigger the single-line fallback prematurely.
    2. Single-line pass: handles the common "Trans Date  Post Date  Description …"
       format found in Chase, Capital One, and similar statements.
    """
    lines = text.splitlines()

    # Pass 1 — split header: "Transaction Posting …\nDate Date …"
    for i in range(len(lines) - 1):
        l1 = lines[i].lower()
        l2 = lines[i + 1].lower()
        l1_words = set(l1.split())
        has_trans_word = bool({"transaction", "trans.", "trans"} & l1_words)
        has_post_word  = bool({"posting", "posted"} & l1_words)
        if has_trans_word and has_post_word and l2.split().count("date") >= 2:
            trans_pos = next(l1.index(w) for w in ("transaction", "trans.", "trans") if w in l1)
            post_pos  = next(l1.index(w) for w in ("posting", "posted")             if w in l1)
            return 1 if trans_pos < post_pos else 0

    # Pass 2 — single-line header: "Trans Date  Post Date  Description …"
    for line in lines:
        has_post  = bool(_POST_DATE_RE.search(line))
        has_trans = bool(_TRANS_DATE_RE.search(line))
        if has_post and has_trans:
            post_pos  = _POST_DATE_RE.search(line).start()
            trans_pos = _TRANS_DATE_RE.search(line).start()
            return 1 if trans_pos < post_pos else 0
        if has_post:
            return 0
    return -1


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


def _parse_table_row(
    row: List[str], source_file: str, account: str, post_date_col: int = -1
) -> Optional[Transaction]:
    """Try to extract a Transaction from a table row."""
    cells = [str(c or "").strip() for c in row]

    txn_date = None
    merchant = ""
    amount = None

    # Use the detected post-date column when available; fall back to first date found.
    if post_date_col >= 0 and post_date_col < len(cells):
        txn_date = _extract_date_from_cell(cells[post_date_col])
    if txn_date is None:
        for cell in cells:
            txn_date = _extract_date_from_cell(cell)
            if txn_date:
                break

    # Amount: take the last cell that looks like a dollar amount
    for cell in reversed(cells):
        m = AMOUNT_PATTERN.search(cell)
        if m:
            amount = parse_amount(m.group(0))
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

    if not candidates:
        return None  # No valid merchant cell — likely a summary/header row, not a transaction

    merchant = max(candidates, key=len)

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


def _parse_text_lines(
    text: str,
    source_file: str,
    account: str,
    year_hint: Optional[int] = None,
    invert_charges: bool = False,
    post_date_position: int = -1,
) -> List[Transaction]:
    """
    Fallback: parse transactions from raw text when no tables found.
    Looks for lines containing a date and a dollar amount.

    year_hint: used to resolve short-form dates like "Apr 13" (no year).
    invert_charges: when True, amounts without a "- " credit prefix are negated.
        Capital One PDFs show charges as positive ("$11.35") and credits as
        "- $500.00"; our sign convention is the opposite.
    post_date_position: 0-based index of the post date among all dates on a
        transaction line (detected from column headers). -1 = use first date found.
    """
    transactions = []
    lines = text.splitlines()

    for line in lines:
        line = line.strip()
        if len(line) < 10:
            continue
        if _SUMMARY_LINE_RE.search(line):
            continue

        date_hits = _collect_dates(line, year_hint)
        if not date_hits:
            continue

        if post_date_position >= 0 and post_date_position < len(date_hits):
            txn_date = date_hits[post_date_position][1]
        else:
            txn_date = date_hits[0][1]

        if not txn_date:
            continue

        amount_matches = AMOUNT_PATTERN.findall(line)
        if not amount_matches:
            continue

        amount = parse_amount(amount_matches[-1])
        if amount is None:
            continue

        # Capital One shows credits as "- $X" (with a space between - and $).
        # Charges have no prefix.  When invert_charges is True we negate charges
        # so that they follow our sign convention (negative = debit).
        if invert_charges:
            last_raw = amount_matches[-1]
            idx = line.rfind(last_raw)
            is_credit_prefix = idx >= 2 and line[idx - 2:idx] == "- "
            if not is_credit_prefix:
                amount = -amount

        # Merchant = everything left after removing dates and amounts.
        clean = re.sub(r"-?\$?\s*[\d,]+\.\d{2}", "", line)
        for pattern in DATE_PATTERNS:
            clean = re.sub(pattern, "", clean)
        if year_hint:
            clean = SHORT_DATE_PATTERN.sub("", clean)
            clean = NUMERIC_SHORT_DATE_PATTERN.sub("", clean)
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

            # Derive year from the most recent full date in the header (used to resolve
            # short-form dates like "Apr 13" that appear in Capital One statements).
            year_hint: Optional[int] = None
            _header_dates = []
            for _pat in DATE_PATTERNS[:-1]:  # skip day-Mon-yy (less reliable)
                for _m in re.finditer(_pat, header_text):
                    _d = _parse_date(_m.group(1))
                    if _d:
                        _header_dates.append(_d)
            if _header_dates:
                year_hint = max(_header_dates).year

            # Credit card statements (Chase, Capital One, BofA Visa, …) list charges as
            # positive amounts and payments as negative — the inverse of our sign convention.
            # Chase/Capital One say "credit card" explicitly; BofA Visa says "Visa Signature".
            _h = header_text.lower()
            invert_charges = (
                "credit card" in _h or
                "visa"        in _h or
                "mastercard"  in _h
            )

            for page_num, page in enumerate(pdf.pages, 1):
                try:
                    # --- Strategy 1: table extraction ---
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue
                        post_date_col = _find_post_date_col(table)
                        for row in table:
                            if not row or not _is_transaction_row(row):
                                continue
                            t = _parse_table_row(
                                row, result.source_file, account_used,
                                post_date_col=post_date_col,
                            )
                            if t:
                                transactions.append(t)

                    # Accumulate text for fallback
                    page_text = page.extract_text() or ""
                    all_text += page_text + "\n"

                except Exception as e:
                    errors.append(f"Page {page_num}: {str(e)}")

            # --- Strategy 2: text fallback if tables yielded nothing ---
            if not transactions and all_text.strip():
                post_date_position = _detect_post_date_position(all_text)
                transactions = _parse_text_lines(
                    all_text, result.source_file, account_used,
                    year_hint=year_hint, invert_charges=invert_charges,
                    post_date_position=post_date_position,
                )
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
