"""
Sample data generator — all data is 100% synthetic.
No real names, accounts, card numbers, or PII of any kind.

write_sample_files() — used by tests; writes static 2024 fixtures with known row counts.
write_demo_files()   — used by --demo mode; writes dynamic 6-month data for a realistic dashboard.
"""
import csv
import io
import calendar
from datetime import date
from pathlib import Path


# ── Static fixtures (used by tests) ───────────────────────────────────────────

CHASE_BANK_CSV = """\
Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
DEBIT,01/03/2024,WHOLE FOODS MARKET #123,-89.47,ACH_DEBIT,2410.53,
DEBIT,01/05/2024,NETFLIX.COM,-15.99,ACH_DEBIT,2394.54,
CREDIT,01/10/2024,PAYROLL DIRECT DEP,3200.00,ACH_CREDIT,5594.54,
DEBIT,01/12/2024,SHELL OIL 12345,-52.10,POS,5542.44,
DEBIT,01/15/2024,AMAZON.COM*AB1CD2,-34.99,ACH_DEBIT,5507.45,
DEBIT,01/18/2024,CITY WATER UTILITY,-78.25,ACH_DEBIT,5429.20,
DEBIT,01/20/2024,TRADER JOES #456,-112.34,POS,5316.86,
DEBIT,01/22/2024,SPOTIFY,-9.99,ACH_DEBIT,5306.87,
DEBIT,01/25/2024,CVS PHARMACY #789,-23.50,POS,5283.37,
DEBIT,01/28/2024,ELECTRIC COMPANY,-145.00,ACH_DEBIT,5138.37,
"""

CHASE_CREDIT_CSV = """\
Transaction Date,Post Date,Description,Category,Type,Amount,Memo
01/02/2024,01/03/2024,UBER *TRIP,Travel,Sale,-12.50,
01/04/2024,01/05/2024,CHIPOTLE MEXICAN GRILL,Food & Drink,Sale,-13.85,
01/06/2024,01/07/2024,COSTCO WHOLESALE,Shopping,Sale,-187.23,
01/08/2024,01/09/2024,DELTA AIR LINES,Travel,Sale,-420.00,
01/11/2024,01/12/2024,WALGREENS #1234,Health & Wellness,Sale,-18.99,
01/14/2024,01/15/2024,APPLE.COM/BILL,Entertainment,Sale,-14.99,
01/16/2024,01/17/2024,STARBUCKS #5678,Food & Drink,Sale,-6.75,
01/19/2024,01/20/2024,BEST BUY #9012,Shopping,Sale,-299.99,
01/22/2024,01/23/2024,PAYMENT THANK YOU,,Payment,500.00,
01/26/2024,01/27/2024,LYFT *RIDE,Travel,Sale,-9.50,
"""

BOFA_BANK_CSV = """\
Date,Description,Amount,Running Bal.
01/02/2024,ZELLE PAYMENT FROM FRIEND,250.00,3250.00
01/04/2024,GROCERY OUTLET,-45.67,3204.33
01/07/2024,ATM WITHDRAWAL,-200.00,3004.33
01/09/2024,DIRECT DEPOSIT EMPLOYER,2800.00,5804.33
01/11/2024,PLANET FITNESS,-24.99,5779.34
01/13/2024,TARGET #1234,-67.89,5711.45
01/17/2024,COMCAST CABLE,-89.99,5621.46
01/21/2024,ONLINE TRANSFER OUT,-500.00,5121.46
01/24/2024,CHEVRON GAS STATION,-48.32,5073.14
01/29/2024,RESTAURANT LOCAL,-35.00,5038.14
"""

GENERIC_CSV = """\
date,merchant,amount,notes
2024-01-03,Office Supplies Store,-45.00,Pens and paper
2024-01-05,Lunch with Team,-78.50,Team lunch
2024-01-08,Hardware Supplies,-123.00,Project materials
2024-01-10,Freelance Invoice Received,1500.00,Client payment
2024-01-14,Parking Garage,-18.00,Downtown meeting
2024-01-17,Internet Service,-59.99,Monthly bill
2024-01-20,Software Subscription,-29.00,Annual plan
2024-01-23,Conference Registration,-199.00,Industry event
2024-01-26,Mileage Reimbursement,87.50,Client visits
2024-01-29,Printing Services,-32.00,Marketing materials
"""


def write_sample_files(output_dir: str):
    """Write static test fixtures to output_dir. Row counts are stable for tests."""
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    files = {
        "chase_bank_sample.csv":   CHASE_BANK_CSV,
        "chase_credit_sample.csv": CHASE_CREDIT_CSV,
        "bofa_bank_sample.csv":    BOFA_BANK_CSV,
        "generic_sample.csv":      GENERIC_CSV,
    }
    written = []
    for name, content in files.items():
        p = base / name
        p.write_text(content.strip())
        written.append(str(p))
    return written


def make_bad_csv(output_dir: str) -> str:
    """A malformed CSV for error-handling tests."""
    content = "Date,Description,Amount\nNOT_A_DATE,Some Merchant,not_a_number\n01/05/2024,Valid Merchant,-25.00\n"
    p = Path(output_dir) / "malformed_sample.csv"
    p.write_text(content)
    return str(p)


# ── Dynamic demo data (used by --demo mode) ────────────────────────────────────

def _mdy(months_ago: int, day: int) -> str:
    """MM/DD/YYYY date string for `day` of the month `months_ago` months back."""
    today = date.today()
    month = today.month - months_ago
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(day, calendar.monthrange(year, month)[1])
    return f"{month:02d}/{day:02d}/{year}"


def _build_demo_chase_bank() -> str:
    """Chase Bank expenses spanning 6 months — no income rows mixed in."""
    rows = [
        ("Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"),
        ("DEBIT", _mdy(5, 3),  "WHOLE FOODS MARKET #123", "-89.47",  "ACH_DEBIT", "2410.53", ""),
        ("DEBIT", _mdy(5, 8),  "NETFLIX.COM",             "-15.99",  "ACH_DEBIT", "2394.54", ""),
        ("DEBIT", _mdy(5, 12), "SHELL OIL 12345",         "-52.10",  "POS",       "2342.44", ""),
        ("DEBIT", _mdy(5, 18), "CITY WATER UTILITY",      "-78.25",  "ACH_DEBIT", "2264.19", ""),
        ("DEBIT", _mdy(5, 22), "TRADER JOES #456",        "-112.34", "POS",       "2151.85", ""),
        ("DEBIT", _mdy(5, 28), "ELECTRIC COMPANY",        "-145.00", "ACH_DEBIT", "2006.85", ""),
        ("DEBIT", _mdy(4, 4),  "AMAZON.COM*AB1CD2",       "-34.99",  "ACH_DEBIT", "1971.86", ""),
        ("DEBIT", _mdy(4, 9),  "SPOTIFY",                 "-9.99",   "ACH_DEBIT", "1961.87", ""),
        ("DEBIT", _mdy(4, 15), "WHOLE FOODS MARKET #123", "-95.20",  "POS",       "1866.67", ""),
        ("DEBIT", _mdy(4, 20), "NETFLIX.COM",             "-15.99",  "ACH_DEBIT", "1850.68", ""),
        ("DEBIT", _mdy(4, 25), "SHELL OIL 12345",         "-61.40",  "POS",       "1789.28", ""),
        ("DEBIT", _mdy(3, 3),  "TRADER JOES #456",        "-108.90", "POS",       "1680.38", ""),
        ("DEBIT", _mdy(3, 9),  "ELECTRIC COMPANY",        "-138.50", "ACH_DEBIT", "1541.88", ""),
        ("DEBIT", _mdy(3, 14), "CITY WATER UTILITY",      "-72.00",  "ACH_DEBIT", "1469.88", ""),
        ("DEBIT", _mdy(3, 19), "AMAZON.COM*XY9ZW1",       "-47.99",  "ACH_DEBIT", "1421.89", ""),
        ("DEBIT", _mdy(3, 24), "SPOTIFY",                 "-9.99",   "ACH_DEBIT", "1411.90", ""),
        ("DEBIT", _mdy(2, 4),  "WHOLE FOODS MARKET #123", "-102.75", "POS",       "1309.15", ""),
        ("DEBIT", _mdy(2, 8),  "NETFLIX.COM",             "-15.99",  "ACH_DEBIT", "1293.16", ""),
        ("DEBIT", _mdy(2, 13), "SHELL OIL 12345",         "-55.80",  "POS",       "1237.36", ""),
        ("DEBIT", _mdy(2, 18), "ELECTRIC COMPANY",        "-152.00", "ACH_DEBIT", "1085.36", ""),
        ("DEBIT", _mdy(2, 23), "CVS PHARMACY #789",       "-31.20",  "POS",       "1054.16", ""),
        ("DEBIT", _mdy(1, 2),  "TRADER JOES #456",        "-119.45", "POS",       "934.71",  ""),
        ("DEBIT", _mdy(1, 7),  "SPOTIFY",                 "-9.99",   "ACH_DEBIT", "924.72",  ""),
        ("DEBIT", _mdy(1, 12), "CITY WATER UTILITY",      "-80.00",  "ACH_DEBIT", "844.72",  ""),
        ("DEBIT", _mdy(1, 17), "AMAZON.COM*PQ3RS4",       "-28.50",  "ACH_DEBIT", "816.22",  ""),
        ("DEBIT", _mdy(1, 22), "ELECTRIC COMPANY",        "-141.75", "ACH_DEBIT", "674.47",  ""),
        ("DEBIT", _mdy(0, 3),  "WHOLE FOODS MARKET #123", "-88.30",  "POS",       "586.17",  ""),
        ("DEBIT", _mdy(0, 7),  "NETFLIX.COM",             "-15.99",  "ACH_DEBIT", "570.18",  ""),
        ("DEBIT", _mdy(0, 10), "SHELL OIL 12345",         "-49.60",  "POS",       "520.58",  ""),
    ]
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


def _build_demo_chase_credit() -> str:
    """Chase Credit charges spanning 6 months — no payment rows mixed in."""
    rows = [
        ("Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"),
        (_mdy(5, 4),  _mdy(5, 5),  "UBER *TRIP",             "Travel",            "Sale", "-12.50",  ""),
        (_mdy(5, 9),  _mdy(5, 10), "CHIPOTLE MEXICAN GRILL", "Food & Drink",      "Sale", "-13.85",  ""),
        (_mdy(5, 14), _mdy(5, 15), "COSTCO WHOLESALE",       "Shopping",          "Sale", "-187.23", ""),
        (_mdy(5, 20), _mdy(5, 21), "WALGREENS #1234",        "Health & Wellness", "Sale", "-18.99",  ""),
        (_mdy(4, 3),  _mdy(4, 4),  "STARBUCKS #5678",        "Food & Drink",      "Sale", "-6.75",   ""),
        (_mdy(4, 8),  _mdy(4, 9),  "APPLE.COM/BILL",         "Entertainment",     "Sale", "-14.99",  ""),
        (_mdy(4, 13), _mdy(4, 14), "LYFT *RIDE",             "Travel",            "Sale", "-9.50",   ""),
        (_mdy(4, 18), _mdy(4, 19), "DELTA AIR LINES",        "Travel",            "Sale", "-420.00", ""),
        (_mdy(3, 5),  _mdy(3, 6),  "CHIPOTLE MEXICAN GRILL", "Food & Drink",      "Sale", "-11.50",  ""),
        (_mdy(3, 10), _mdy(3, 11), "BEST BUY #9012",         "Shopping",          "Sale", "-299.99", ""),
        (_mdy(3, 16), _mdy(3, 17), "UBER *TRIP",             "Travel",            "Sale", "-18.75",  ""),
        (_mdy(3, 22), _mdy(3, 23), "WALGREENS #1234",        "Health & Wellness", "Sale", "-22.40",  ""),
        (_mdy(2, 2),  _mdy(2, 3),  "STARBUCKS #5678",        "Food & Drink",      "Sale", "-7.25",   ""),
        (_mdy(2, 7),  _mdy(2, 8),  "APPLE.COM/BILL",         "Entertainment",     "Sale", "-14.99",  ""),
        (_mdy(2, 12), _mdy(2, 13), "COSTCO WHOLESALE",       "Shopping",          "Sale", "-210.50", ""),
        (_mdy(2, 19), _mdy(2, 20), "LYFT *RIDE",             "Travel",            "Sale", "-11.00",  ""),
        (_mdy(1, 4),  _mdy(1, 5),  "CHIPOTLE MEXICAN GRILL", "Food & Drink",      "Sale", "-14.25",  ""),
        (_mdy(1, 9),  _mdy(1, 10), "UBER *TRIP",             "Travel",            "Sale", "-22.00",  ""),
        (_mdy(1, 15), _mdy(1, 16), "WALGREENS #1234",        "Health & Wellness", "Sale", "-19.80",  ""),
        (_mdy(1, 21), _mdy(1, 22), "STARBUCKS #5678",        "Food & Drink",      "Sale", "-8.50",   ""),
        (_mdy(0, 2),  _mdy(0, 3),  "APPLE.COM/BILL",         "Entertainment",     "Sale", "-14.99",  ""),
        (_mdy(0, 6),  _mdy(0, 7),  "CHIPOTLE MEXICAN GRILL", "Food & Drink",      "Sale", "-12.75",  ""),
        (_mdy(0, 9),  _mdy(0, 10), "LYFT *RIDE",             "Travel",            "Sale", "-15.50",  ""),
    ]
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


def _build_demo_income() -> str:
    """BofA-format income CSV — written to income/ folder."""
    rows = [
        ("Date", "Description", "Amount", "Running Bal."),
        (_mdy(5, 10), "PAYROLL DIRECT DEPOSIT",     "3200.00",  "3200.00"),
        (_mdy(5, 25), "FREELANCE PAYMENT CLIENT A",  "850.00",  "4050.00"),
        (_mdy(4, 10), "PAYROLL DIRECT DEPOSIT",     "3200.00",  "7250.00"),
        (_mdy(3, 10), "PAYROLL DIRECT DEPOSIT",     "3200.00", "10450.00"),
        (_mdy(3, 20), "FREELANCE PAYMENT CLIENT B", "1200.00", "11650.00"),
        (_mdy(2, 10), "PAYROLL DIRECT DEPOSIT",     "3200.00", "14850.00"),
        (_mdy(1, 10), "PAYROLL DIRECT DEPOSIT",     "3200.00", "18050.00"),
        (_mdy(1, 22), "FREELANCE PAYMENT CLIENT A",  "650.00", "18700.00"),
        (_mdy(0, 10), "PAYROLL DIRECT DEPOSIT",     "3200.00", "21900.00"),
    ]
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


def write_demo_files(output_dir: str):
    """Write dynamic 6-month demo CSVs for --demo mode."""
    expense_dir = Path(output_dir)
    expense_dir.mkdir(parents=True, exist_ok=True)
    income_dir = expense_dir.parent / "income"
    income_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for name, content in [
        ("chase_bank_sample.csv",   _build_demo_chase_bank()),
        ("chase_credit_sample.csv", _build_demo_chase_credit()),
    ]:
        p = expense_dir / name
        p.write_text(content.strip())
        written.append(str(p))

    p = income_dir / "income_sample.csv"
    p.write_text(_build_demo_income().strip())
    written.append(str(p))
    return written
