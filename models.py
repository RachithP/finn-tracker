"""
Expense Tracker - Core Data Models
All data stays local. No network calls. No PII displayed.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
import re


# Built-in category list (user can extend)
DEFAULT_CATEGORIES = [
    "Food & Dining",
    "Groceries",
    "Transportation",
    "Gas & Fuel",
    "Shopping",
    "Entertainment",
    "Health & Medical",
    "Utilities",
    "Housing & Rent",
    "Travel",
    "Insurance",
    "Subscriptions",
    "Income",
    "Payments",
    "Fees & Charges",
    "Uncategorized",
]


_AUTOCAT_RULES = [
    (re.compile(r'PAYROLL|DIRECT DEP|DIRECT DEPOSIT|FREELANCE INVOICE|MILEAGE REIMB|ZELLE PAYMENT FROM', re.I), "Income"),
    (re.compile(r'WHOLE FOODS|TRADER JOE|COSTCO|GROCERY OUTLET', re.I), "Groceries"),
    (re.compile(r'NETFLIX|SPOTIFY|APPLE\.COM|SOFTWARE SUBSCR', re.I), "Subscriptions"),
    (re.compile(r'SHELL OIL|CHEVRON|GAS STATION', re.I), "Gas & Fuel"),
    (re.compile(r'AMAZON|BEST BUY|TARGET|OFFICE SUPPLIES|HARDWARE|PRINTING', re.I), "Shopping"),
    (re.compile(r'WATER UTILITY|ELECTRIC COMPANY|COMCAST|INTERNET SERVICE', re.I), "Utilities"),
    (re.compile(r'UBER|LYFT|PARKING', re.I), "Transportation"),
    (re.compile(r'CHIPOTLE|STARBUCKS|RESTAURANT|LUNCH WITH', re.I), "Food & Dining"),
    (re.compile(r'CVS|WALGREENS|PLANET FITNESS', re.I), "Health & Medical"),
    (re.compile(r'DELTA AIR', re.I), "Travel"),
    (re.compile(r'PAYMENT THANK YOU|ATM WITHDRAWAL|ONLINE TRANSFER', re.I), "Payments"),
    (re.compile(r'MEMBER FEE|ANNUAL FEE|LATE FEE|INTEREST CHARGE|FOREIGN TRANSACTION FEE', re.I), "Fees & Charges"),
]


def autocat(merchant: str) -> str:
    """Apply static categorization rules to a merchant name. Mirrors JS autocat() in index.html."""
    for pattern, category in _AUTOCAT_RULES:
        if pattern.search(merchant):
            return category
    return "Uncategorized"


def mask_sensitive(value: str) -> str:
    """Mask account numbers, card numbers, SSNs from any string."""
    if not value:
        return value
    # Card numbers (16 digits)
    value = re.sub(r'\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b', '****-****-****-####', value)
    # Account numbers (8-12 digits standalone)
    value = re.sub(r'\b\d{8,12}\b', '***######', value)
    # SSN
    value = re.sub(r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b', '***-**-####', value)
    return value


@dataclass
class Transaction:
    """A single normalized expense/income transaction."""
    date: date
    merchant: str          # Merchant or description
    amount: float          # Negative = charge/debit (expense), Positive = payment/credit (income)
    category: str = "Uncategorized"
    account: str = ""      # Masked account label (e.g. "Chase ••4231")
    source_file: str = ""  # Filename it came from
    raw_description: str = "" # Original text before normalization
    transaction_id: str = ""  # Optional bank-assigned ID

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "merchant": mask_sensitive(self.merchant),
            "amount": round(self.amount, 2),
            "category": self.category,
            "account": self.account,
            "source_file": self.source_file,
            "transaction_id": self.transaction_id,
        }

    def __repr__(self):
        return (f"Transaction({self.date} | {mask_sensitive(self.merchant)[:30]} "
                f"| ${self.amount:.2f} | {self.category})")


@dataclass
class ParseResult:
    """Result returned by any parser."""
    transactions: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    source_file: str = ""
    parser_used: str = ""
    row_count: int = 0

    @property
    def success(self) -> bool:
        return len(self.transactions) > 0

    @property
    def summary(self) -> dict:
        if not self.transactions:
            return {"total": 0, "count": 0, "errors": self.errors}
        amounts = [t.amount for t in self.transactions]
        return {
            "count": len(self.transactions),
            "total_expenses": round(abs(sum(a for a in amounts if a < 0)), 2),
            "total_income": round(sum(a for a in amounts if a > 0), 2),
            "net": round(sum(amounts), 2),
            "source_file": self.source_file,
            "parser_used": self.parser_used,
            "errors": self.errors,
        }
