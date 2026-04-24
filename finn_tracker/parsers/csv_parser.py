"""
CSV Parser — supports multiple bank/card formats with auto-detection.
Data never leaves local disk. No network calls.
"""
import csv
import io
import re
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import pandas as pd

from finn_tracker.models import Transaction, ParseResult, mask_sensitive


# ── Format signatures ──────────────────────────────────────────────────────────

FORMATS = {
    "chase_bank": {
        "required_cols": {"Details", "Posting Date", "Description", "Amount", "Type", "Balance"},
        "date_col": "Posting Date",
        "merchant_col": "Description",
        "amount_col": "Amount",
        "date_fmt": "%m/%d/%Y",
        "negate": False,
    },
    "chase_credit": {
        "required_cols": {"Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"},
        "date_col": "Transaction Date",
        "merchant_col": "Description",
        "amount_col": "Amount",
        "date_fmt": "%m/%d/%Y",
        "negate": False,  # Chase credit CSV: charges are already negative, payments positive
    },
    "bofa_bank": {
        "required_cols": {"Date", "Description", "Amount", "Running Bal."},
        "date_col": "Date",
        "merchant_col": "Description",
        "amount_col": "Amount",
        "date_fmt": "%m/%d/%Y",
        "negate": False,
    },
    "bofa_credit": {
        "required_cols": {"Posted Date", "Reference Number", "Payee", "Address", "Amount"},
        "date_col": "Posted Date",
        "merchant_col": "Payee",
        "amount_col": "Amount",
        "date_fmt": "%m/%d/%Y",
        "negate": False,  # BofA credit CSV: charges are negative, payments/refunds positive (same as Chase)
    },
    "capital_one": {
        # Debit/Credit are separate columns; Debit = charge → stored negative, Credit = payment/refund → stored positive
        "required_cols": {"Transaction Date", "Posted Date", "Card No.", "Description", "Category", "Debit", "Credit"},
        "date_col": "Transaction Date",
        "merchant_col": "Description",
        "amount_col": None,       # handled specially via debit_col/credit_col
        "debit_col": "Debit",
        "credit_col": "Credit",
        "date_fmt": "%Y-%m-%d",
        "negate": False,
    },
    "generic": {
        # Fallback: look for date/description/amount-like columns
        "required_cols": set(),
        "date_col": None,
        "merchant_col": None,
        "amount_col": None,
        "date_fmt": None,
        "negate": False,
    },
}

DATE_FORMATS = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y"]

DATE_SYNONYMS = ["date", "transaction date", "posting date", "posted date", "trans date"]
MERCHANT_SYNONYMS = ["description", "merchant", "payee", "memo", "name", "transaction"]
AMOUNT_SYNONYMS = ["amount", "debit", "credit", "charge", "transaction amount"]

BANK_BY_FORMAT = {
    "chase_bank":   "Chase",
    "chase_credit": "Chase",
    "bofa_bank":    "BofA",
    "bofa_credit":  "BofA",
    "capital_one":  "Capital One",
}


def _extract_last4_from_name(name: str) -> str:
    """Find the most likely 4-digit account suffix in a filename.
    Prefers the LAST standalone 4-digit group (avoids being fooled by dates)."""
    groups = re.findall(r'(?<!\d)(\d{4})(?!\d)', name)
    return groups[-1] if groups else ""


def _format_account_label(bank: str, last4: str, fallback: str = "") -> str:
    if bank and last4:
        return f"{bank} ••{last4}"
    if bank:
        return bank
    return fallback


def _parse_amount(raw: str) -> Optional[float]:
    """Parse amount strings like '$1,234.56', '-$50.00', '(100.00)'."""
    if not raw:
        return None
    s = str(raw).strip().replace(",", "").replace("$", "").replace(" ", "")
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        val = float(s)
        if val != val:  # NaN check
            return None
        return -val if negative else val
    except ValueError:
        return None


def _parse_date(raw: str, fmt: Optional[str] = None) -> Optional[date]:
    """Try multiple date formats."""
    raw = str(raw).strip()
    fmts = [fmt] + DATE_FORMATS if fmt else DATE_FORMATS
    for f in fmts:
        if not f:
            continue
        try:
            return datetime.strptime(raw, f).date()
        except ValueError:
            continue
    return None


def _detect_format(columns: set) -> str:
    """Identify which bank format matches the column set."""
    for fmt_name, cfg in FORMATS.items():
        if fmt_name == "generic":
            continue
        if cfg["required_cols"].issubset(columns):
            return fmt_name
    return "generic"


def _detect_generic_cols(df: pd.DataFrame):
    """For unknown CSVs, guess which columns are date/merchant/amount."""
    cols_lower = {c.lower(): c for c in df.columns}
    date_col = next((cols_lower[k] for k in DATE_SYNONYMS if k in cols_lower), None)
    merchant_col = next((cols_lower[k] for k in MERCHANT_SYNONYMS if k in cols_lower), None)
    amount_col = next((cols_lower[k] for k in AMOUNT_SYNONYMS if k in cols_lower), None)
    return date_col, merchant_col, amount_col


def _skip_header_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Some bank CSVs have 1–3 info rows before real headers. Drop them."""
    # If first row looks like a label row (mostly empty or non-numeric amounts), skip it
    return df.dropna(how="all").reset_index(drop=True)


def parse_csv(file_path: str, account_label: str = "") -> ParseResult:
    """
    Parse a CSV file from any supported bank or generic format.

    Args:
        file_path: Absolute path to the CSV on disk.
        account_label: Optional masked label like 'Chase ••4231'.

    Returns:
        ParseResult with normalized Transaction objects.
    """
    result = ParseResult(source_file=Path(file_path).name)
    transactions = []
    errors = []

    try:
        # Read raw to detect encoding and skip BOM
        raw = Path(file_path).read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")

        df = pd.read_csv(io.StringIO(text), dtype=str)
        df = _skip_header_rows(df)
        df.columns = [str(c).strip() for c in df.columns]

        col_set = set(df.columns)
        fmt_name = _detect_format(col_set)
        cfg = FORMATS[fmt_name]

        if fmt_name == "generic":
            date_col, merchant_col, amount_col = _detect_generic_cols(df)
            date_fmt = None
        else:
            date_col = cfg["date_col"]
            merchant_col = cfg["merchant_col"]
            amount_col = cfg["amount_col"]
            date_fmt = cfg["date_fmt"]

        debit_col = cfg.get("debit_col")
        credit_col = cfg.get("credit_col")
        split_columns = bool(debit_col and credit_col)

        if not date_col or not merchant_col or (not amount_col and not split_columns):
            result.errors.append(
                f"Could not identify required columns (date/merchant/amount) in {result.source_file}"
            )
            result.parser_used = "csv_auto"
            return result

        negate = cfg.get("negate", False)

        # Derive a clean account label: "<Bank> ••<last4>" or use the provided account_label
        bank = BANK_BY_FORMAT.get(fmt_name, "")
        file_last4 = _extract_last4_from_name(Path(file_path).stem)
        if bank and file_last4:
            file_account = _format_account_label(bank, file_last4)
        elif account_label:
            file_account = account_label
        elif bank:
            file_account = bank
        else:
            file_account = Path(file_path).stem

        for idx, row in df.iterrows():
            try:
                raw_date = str(row.get(date_col, "")).strip()
                raw_merchant = str(row.get(merchant_col, "")).strip()

                if not raw_date or raw_date.lower() == "nan":
                    continue

                parsed_date = _parse_date(raw_date, date_fmt)
                if parsed_date is None:
                    errors.append(f"Row {idx}: unparseable date '{raw_date}'")
                    continue

                if split_columns:
                    debit = _parse_amount(str(row.get(debit_col, "")).strip())
                    credit = _parse_amount(str(row.get(credit_col, "")).strip())
                    if debit is not None:
                        amount = -debit      # charge: stored as negative
                    elif credit is not None:
                        amount = credit      # payment/refund: stored as positive
                    else:
                        errors.append(f"Row {idx}: no debit or credit value")
                        continue
                else:
                    raw_amount = str(row.get(amount_col, "")).strip()
                    if not raw_amount or raw_amount.lower() == "nan":
                        continue
                    amount = _parse_amount(raw_amount)
                    if amount is None:
                        errors.append(f"Row {idx}: unparseable amount '{raw_amount}'")
                        continue
                    if negate:
                        amount = -amount

                merchant = mask_sensitive(raw_merchant) if raw_merchant else "Unknown"

                # Per-row account for Capital One (Card No. column); file-level otherwise.
                if fmt_name == "capital_one":
                    card_no = str(row.get("Card No.", "")).strip()
                    row_last4 = card_no[-4:] if len(card_no) >= 4 and card_no[-4:].isdigit() else file_last4
                    row_account = _format_account_label("Capital One", row_last4, file_account)
                else:
                    row_account = file_account

                t = Transaction(
                    date=parsed_date,
                    merchant=merchant,
                    amount=amount,
                    category="Uncategorized",
                    account=row_account,
                    source_file=result.source_file,
                    raw_description=raw_merchant,
                )
                transactions.append(t)

            except Exception as e:
                errors.append(f"Row {idx}: {str(e)}")

        result.transactions = transactions
        result.errors = errors
        result.parser_used = f"csv_{fmt_name}"
        result.row_count = len(df)

    except Exception as e:
        result.errors.append(f"File-level error: {str(e)}")
        result.parser_used = "csv_auto"

    return result
