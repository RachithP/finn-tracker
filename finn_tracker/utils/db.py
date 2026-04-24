"""
utils/db.py — Shared data access layer.

Used by both app.py (/chat context building) and mcp_server.py.
No Flask dependency. Reads all state directly from SQLite — safe to call
while the Flask app is running since the DB uses WAL mode.
"""
import hashlib
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

# ── Project root ──────────────────────────────────────────────────────────────
# Module-level fallback used only when db_path is not passed explicitly.
# In normal operation, callers pass the path resolved from EXPENSE_TRACKER_DATA
# (set by the CLI before importing app.py). This constant is kept for
# mcp_server.py which resolves its own path at startup.
DB_PATH = Path.home() / "Documents" / "finn-tracker" / "finn_tracker.db"


def _get_default_folders() -> List[Path]:
    """Return auto-scan folders, resolved from EXPENSE_TRACKER_DATA env var at call time."""
    import os
    data_dir = Path(os.environ.get("EXPENSE_TRACKER_DATA", str(Path.home() / "Documents" / "finn-tracker")))
    return [data_dir / "expense", data_dir / "income"]

from finn_tracker.models import DEFAULT_CATEGORIES, mask_sensitive, autocat


# ── DB connection ─────────────────────────────────────────────────────────────

def _db_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ── Transaction ID (mirrors app.py _make_txn_id) ─────────────────────────────

def make_txn_id(t: dict) -> str:
    """Stable 12-char MD5 hash of (date, merchant, amount, account)."""
    key = f"{t.get('date')}|{t.get('merchant')}|{t.get('amount')}|{t.get('account')}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ── DB reads ──────────────────────────────────────────────────────────────────

def load_category_overrides(db_path: Path = None) -> Dict[str, str]:
    """Load txn_id → category mapping from the DB."""
    db_path = db_path or DB_PATH
    try:
        with _db_conn(db_path) as conn:
            return {
                row["txn_id"]: row["category"]
                for row in conn.execute("SELECT txn_id, category FROM category_overrides")
            }
    except Exception:
        return {}


def save_category_override(txn_id: str, category: str, db_path: Path = None) -> None:
    """Upsert a category override into the DB."""
    db_path = db_path or DB_PATH
    ts = datetime.now().isoformat()
    with _db_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO category_overrides (txn_id, category, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(txn_id) DO UPDATE SET"
            " category=excluded.category, updated_at=excluded.updated_at",
            (txn_id, category, ts),
        )


def load_learned_rules(db_path: Path = None) -> List[dict]:
    """Load merchant-pattern → category rules from the DB."""
    db_path = db_path or DB_PATH
    try:
        with _db_conn(db_path) as conn:
            return [
                {"pattern": r["pattern"], "category": r["category"]}
                for r in conn.execute("SELECT pattern, category FROM learned_rules ORDER BY id")
            ]
    except Exception:
        return []


def get_categories(db_path: Path = None) -> List[str]:
    """Return all categories: built-in defaults + user-added custom ones."""
    db_path = db_path or DB_PATH
    custom: List[str] = []
    try:
        with _db_conn(db_path) as conn:
            custom = [
                row["name"]
                for row in conn.execute("SELECT name FROM custom_categories ORDER BY id")
            ]
    except Exception:
        pass
    all_cats = list(DEFAULT_CATEGORIES)
    for c in custom:
        if c not in all_cats:
            all_cats.append(c)
    return all_cats


def _load_user_transactions(db_path: Path, overrides: Dict[str, str]) -> List[dict]:
    """Load manually imported transactions from the DB and apply category overrides."""
    try:
        with _db_conn(db_path) as conn:
            txns = [
                json.loads(row["txn_json"])
                for row in conn.execute("SELECT txn_json FROM user_transactions ORDER BY id")
            ]
    except Exception:
        return []

    for t in txns:
        tid = t.get("txn_id") or make_txn_id(t)
        t["txn_id"] = tid
        if tid in overrides:
            t["category"] = overrides[tid]
    return txns


# ── Folder scanner ────────────────────────────────────────────────────────────

def scan_folders(
    overrides: Dict[str, str] = None,
    folders: List[Path] = None,
) -> List[dict]:
    """
    Scan CSV/PDF files from the default (or given) folders.
    Applies category overrides from the DB.
    Missing folders are silently skipped.
    """
    from finn_tracker.ingest import ingest_file  # local import — avoids circular issues at module level

    overrides = overrides or {}
    folders = folders or _get_default_folders()
    txns: List[dict] = []

    for folder in folders:
        if not folder.is_dir():
            continue
        source_folder = folder.name  # "expense" or "income"
        for fp in sorted(p for p in folder.iterdir() if p.suffix.lower() in {".csv", ".pdf"}):
            result = ingest_file(str(fp), account_label=fp.stem)
            for t in result.transactions:
                d = t.to_dict()
                if not d.get("account"):
                    d["account"] = fp.stem
                d["source_folder"] = source_folder
                tid = make_txn_id(d)
                d["txn_id"] = tid
                if tid in overrides:
                    d["category"] = overrides[tid]
                txns.append(d)

    return txns


# ── Deduplication (mirrors app.py _dedup) ─────────────────────────────────────

def dedup(txns: List[dict]) -> List[dict]:
    """Deduplicate by (date, merchant lower, signed amount). Preserves first occurrence."""
    seen, out = set(), []
    for t in txns:
        key = (
            t.get("date"),
            t.get("merchant", "").lower().strip(),
            round(float(t.get("amount", 0)), 2),
        )
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def _extract_pattern(merchant: str) -> str:
    """Normalize a merchant name to a stable matching pattern for learned rules."""
    s = merchant.upper()
    s = re.sub(r'^(SQ\s*\*|TST\s*\*|PP\s*\*|SP\s+)', '', s)
    s = re.sub(r'\s*[-\u2013].*$', '', s)
    s = s.replace('.', ' ').replace('*', ' ')
    s = re.sub(r'#\d*', '', s)
    s = re.sub(r'(?<=[A-Z])\d+', '', s)
    s = re.sub(r'\b\d{4,}\b', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return ' '.join(s.split()[:2])


# ── Full transaction pipeline ─────────────────────────────────────────────────

def get_all_transactions(db_path: Path = None) -> List[dict]:
    """
    Full pipeline: scan default folders + load user imports from DB,
    merge, deduplicate, and apply category overrides and learned rules.
    """
    db_path = db_path or DB_PATH
    overrides = load_category_overrides(db_path)
    rules = load_learned_rules(db_path)  # [{pattern, category}]
    folder_txns = scan_folders(overrides)
    user_txns = _load_user_transactions(db_path, overrides)
    txns = dedup(folder_txns + user_txns)

    # Apply learned rules then static autocat to transactions still Uncategorized
    if rules:
        for t in txns:
            if t.get("category", "Uncategorized") == "Uncategorized":
                pattern = _extract_pattern(t.get("merchant", ""))
                for rule in rules:
                    if rule["pattern"] == pattern:
                        t["category"] = rule["category"]
                        break

    for t in txns:
        if t.get("category", "Uncategorized") == "Uncategorized":
            t["category"] = autocat(t.get("merchant", ""))

    return txns


# ── Period filtering (ported from JS filterByPeriod in index.html) ────────────

def filter_by_period(
    txns: List[dict],
    period: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[dict]:
    """
    Filter transactions by time period.

    Supported periods: "1m", "3m", "6m", "ytd", "thismonth", "lastmonth", "all", "custom".
    For "custom", pass date_from and date_to as "YYYY-MM-DD" strings.
    """
    if period == "all" or not period:
        return txns

    today = date.today()

    if period == "custom":
        if not date_from or not date_to:
            return txns
        df = date.fromisoformat(date_from)
        dt = date.fromisoformat(date_to)
        return [t for t in txns
                if t.get("date") and df <= date.fromisoformat(t["date"]) <= dt]

    if period == "thismonth":
        prefix = today.strftime("%Y-%m")
        return [t for t in txns if t.get("date", "").startswith(prefix)]

    if period == "lastmonth":
        if today.month == 1:
            last = today.replace(year=today.year - 1, month=12, day=1)
        else:
            last = today.replace(month=today.month - 1, day=1)
        prefix = last.strftime("%Y-%m")
        return [t for t in txns if t.get("date", "").startswith(prefix)]

    if period == "ytd":
        ytd_start = today.replace(month=1, day=1).isoformat()
        return [t for t in txns if t.get("date", "") >= ytd_start]

    months_map = {"1m": 1, "3m": 3, "6m": 6}
    if period in months_map:
        n = months_map[period]
        month = today.month - n
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        cutoff = date(year, month, today.day).isoformat()
        return [t for t in txns if t.get("date", "") >= cutoff]

    return txns  # unknown period → return all


# ── Analytics helpers ─────────────────────────────────────────────────────────

def _is_expense(t: dict) -> bool:
    """True for charge/debit transactions (not income folder, negative amount)."""
    return t.get("source_folder", "expense") != "income" and float(t.get("amount", 0)) < 0


def get_spending_summary(txns: List[dict], period: str = "thismonth") -> dict:
    """
    Spending totals broken down by category for the given period.

    Returns:
        {period, total, count,
         by_category: [{category, total, count, pct}] sorted by total desc}
    """
    filtered = filter_by_period(txns, period)
    expenses = [t for t in filtered if _is_expense(t)]

    by_cat: Dict[str, float] = {}
    cat_count: Dict[str, int] = {}
    for t in expenses:
        cat = t.get("category", "Uncategorized")
        amt = abs(float(t.get("amount", 0)))
        by_cat[cat] = by_cat.get(cat, 0) + amt
        cat_count[cat] = cat_count.get(cat, 0) + 1

    total = sum(by_cat.values())
    breakdown = sorted(
        [
            {
                "category": cat,
                "total": round(amt, 2),
                "count": cat_count[cat],
                "pct": round(amt / total * 100, 1) if total else 0.0,
            }
            for cat, amt in by_cat.items()
        ],
        key=lambda x: x["total"],
        reverse=True,
    )

    return {
        "period": period,
        "total": round(total, 2),
        "count": len(expenses),
        "by_category": breakdown,
    }


def get_top_merchants(
    txns: List[dict],
    period: str = "thismonth",
    limit: int = 10,
) -> List[dict]:
    """
    Top N merchants by total spending in the given period.

    Returns: [{merchant, total, count}] sorted by total desc.
    """
    filtered = filter_by_period(txns, period)
    expenses = [t for t in filtered if _is_expense(t)]

    by_merchant: Dict[str, float] = {}
    merchant_count: Dict[str, int] = {}
    for t in expenses:
        m = t.get("merchant", "Unknown")
        amt = abs(float(t.get("amount", 0)))
        by_merchant[m] = by_merchant.get(m, 0) + amt
        merchant_count[m] = merchant_count.get(m, 0) + 1

    ranked = sorted(by_merchant.items(), key=lambda x: x[1], reverse=True)
    return [
        {"merchant": m, "total": round(amt, 2), "count": merchant_count[m]}
        for m, amt in ranked[:limit]
    ]


def get_monthly_trend(txns: List[dict], months: int = 12) -> List[dict]:
    """
    Month-by-month spending and income totals for the last N months.

    Returns: [{month (YYYY-MM), expenses, income, net}] sorted oldest → newest.
    """
    today = date.today()
    buckets: Dict[str, dict] = {}

    for i in range(months - 1, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        key = f"{y:04d}-{m:02d}"
        buckets[key] = {"month": key, "expenses": 0.0, "income": 0.0, "net": 0.0}

    for t in txns:
        key = t.get("date", "")[:7]
        if key not in buckets:
            continue
        amt = float(t.get("amount", 0))
        if t.get("source_folder") == "income":
            buckets[key]["income"] += amt
        elif amt < 0:
            buckets[key]["expenses"] += abs(amt)

    for v in buckets.values():
        v["expenses"] = round(v["expenses"], 2)
        v["income"] = round(v["income"], 2)
        v["net"] = round(v["income"] - v["expenses"], 2)

    return list(buckets.values())
