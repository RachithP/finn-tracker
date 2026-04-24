"""
Expense Tracker — Flask backend.
All data stays on the local machine. No external network calls.
State is persisted to a local SQLite database (data/finn_tracker.db).
"""
import csv
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from flask import Flask, request, jsonify, Response, stream_with_context

# Suppress Flask/werkzeug startup banner (finn-tracker prints its own message)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

from finn_tracker.ingest import ingest_file
from finn_tracker.models import mask_sensitive, DEFAULT_CATEGORIES, autocat
from finn_tracker.utils.db import _get_default_folders, _extract_pattern

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB


@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "File too large (50 MB limit)"}), 413


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500

# ── Persistence (SQLite) ──────────────────────────────────────────────────────

# Sentinel: tests can set flask_app.DB_PATH = Path(...) to redirect the DB.
# When None, _get_db_path() resolves from EXPENSE_TRACKER_DATA env var.
DB_PATH: Optional[Path] = None


def _get_db_path() -> Path:
    """Return the active DB path (test override > env var > default)."""
    if DB_PATH is not None:
        return DB_PATH
    data_dir = os.environ.get("EXPENSE_TRACKER_DATA", str(Path.home() / "Documents" / "finn-tracker"))
    return Path(data_dir) / "finn_tracker.db"


def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with _db_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS category_overrides (
                txn_id     TEXT PRIMARY KEY,
                category   TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS custom_categories (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_transactions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                txn_id        TEXT UNIQUE NOT NULL,
                source_file   TEXT,
                source_folder TEXT,
                txn_json      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learned_rules (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern    TEXT UNIQUE NOT NULL,
                category   TEXT NOT NULL,
                hit_count  INTEGER DEFAULT 1,
                updated_at TEXT NOT NULL
            );
        """)


def _load_session_from_db() -> None:
    """Populate _session from the DB on startup."""
    with _db_conn() as conn:
        _session["categories"] = {
            row["txn_id"]: row["category"]
            for row in conn.execute("SELECT txn_id, category FROM category_overrides")
        }
        _session["custom_categories"] = [
            row["name"]
            for row in conn.execute("SELECT name FROM custom_categories ORDER BY id")
        ]
        _session["user_transactions"] = [
            json.loads(row["txn_json"])
            for row in conn.execute("SELECT txn_json FROM user_transactions ORDER BY id")
        ]
        _session["learned_rules"] = _db_load_learned_rules(conn)


def _db_save_category(txn_id: str, category: str) -> None:
    ts = datetime.now().isoformat()
    with _db_conn() as conn:
        conn.execute(
            "INSERT INTO category_overrides (txn_id, category, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(txn_id) DO UPDATE SET category=excluded.category, updated_at=excluded.updated_at",
            (txn_id, category, ts),
        )


def _db_save_custom_category(name: str) -> None:
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO custom_categories (name) VALUES (?)", (name,)
        )


def _db_remove_custom_category(name: str) -> None:
    with _db_conn() as conn:
        conn.execute("DELETE FROM custom_categories WHERE name = ?", (name,))



def _db_save_learned_rule(pattern: str, category: str) -> None:
    ts = datetime.now().isoformat()
    with _db_conn() as conn:
        conn.execute(
            "INSERT INTO learned_rules (pattern, category, hit_count, updated_at) VALUES (?, ?, 1, ?)"
            " ON CONFLICT(pattern) DO UPDATE SET category=excluded.category,"
            " hit_count=hit_count+1, updated_at=excluded.updated_at",
            (pattern, category, ts),
        )


def _db_load_learned_rules(conn=None) -> list:
    def _query(c):
        return [{"pattern": r["pattern"], "category": r["category"]}
                for r in c.execute("SELECT pattern, category FROM learned_rules ORDER BY id")]
    if conn is not None:
        return _query(conn)
    with _db_conn() as c:
        return _query(c)


def _db_save_user_transactions(txn_list: List[dict]) -> None:
    """Upsert a list of transaction dicts. Skips any txn_id already stored."""
    rows = [
        (t["txn_id"], t.get("source_file"), t.get("source_folder"), json.dumps(t))
        for t in txn_list
        if "txn_id" in t
    ]
    if not rows:
        return
    with _db_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO user_transactions (txn_id, source_file, source_folder, txn_json)"
            " VALUES (?, ?, ?, ?)",
            rows,
        )


# ── In-memory session state ───────────────────────────────────────────────────
# Loaded from SQLite on startup; written back on every mutation.
_session: Dict[str, Any] = {
    "categories":        {},   # txn_id → category override
    "custom_categories": [],   # user-added category names
    "user_transactions": [],   # transactions imported manually (persisted across restarts)
    "learned_rules":     [],   # [{pattern, category}] — merchant pattern → category rules
}

# ── Default auto-load folders ─────────────────────────────────────────────────

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_txn_id(t: dict) -> str:
    """Stable 12-char hash from (date, merchant, amount, account)."""
    key = f"{t.get('date')}|{t.get('merchant')}|{t.get('amount')}|{t.get('account')}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _enrich(txn_dict: dict) -> dict:
    """Attach txn_id and apply category override, learned rules, then static autocat."""
    tid = _make_txn_id(txn_dict)
    txn_dict["txn_id"] = tid
    if tid in _session["categories"]:
        txn_dict["category"] = _session["categories"][tid]
    if txn_dict.get("category", "Uncategorized") == "Uncategorized":
        pattern = _extract_pattern(txn_dict.get("merchant", ""))
        for rule in _session.get("learned_rules", []):
            if rule["pattern"] == pattern:
                txn_dict["category"] = rule["category"]
                break
    if txn_dict.get("category", "Uncategorized") == "Uncategorized":
        txn_dict["category"] = autocat(txn_dict.get("merchant", ""))
    return txn_dict


def _mask_txns(txns: List[dict]) -> List[dict]:
    """Apply privacy masking to merchant fields before any JSON response."""
    out = []
    for t in txns:
        m = dict(t)
        m["merchant"] = mask_sensitive(t.get("merchant", ""))
        out.append(m)
    return out


def _dedup(txns: List[dict]) -> List[dict]:
    """Deduplicate by (date, merchant lower, signed amount). Preserves first occurrence."""
    seen, out = set(), []
    for t in txns:
        key = (t.get("date"), t.get("merchant", "").lower().strip(),
               round(float(t.get("amount", 0)), 2))
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def _scan_default_folders() -> List[dict]:
    """Fresh scan of auto-load folders on every call. Missing folders are silently skipped."""
    txns: List[dict] = []
    for folder in _get_default_folders():
        if not folder.is_dir():
            print(f"[auto-load] skipping missing folder: {folder}")
            continue
        source_folder = folder.name  # "expense" or "income"
        for fp in sorted(p for p in folder.iterdir() if p.suffix.lower() in {".csv", ".pdf"}):
            result = ingest_file(str(fp), account_label=fp.stem)
            for t in result.transactions:
                d = t.to_dict()
                if not d.get("account"):
                    d["account"] = fp.stem
                d["source_folder"] = source_folder
                txns.append(_enrich(d))
    return _mask_txns(txns)


def _merge_into_session(masked_txns: List[dict]) -> List[dict]:
    """Deduplicate masked_txns against session, persist new ones, return only new."""
    existing_keys = {
        (t["date"], t["merchant"].lower().strip(), round(float(t["amount"]), 2))
        for t in _session["user_transactions"]
    }
    new_txns: List[dict] = []
    for t in masked_txns:
        key = (t["date"], t["merchant"].lower().strip(), round(float(t["amount"]), 2))
        if key not in existing_keys:
            _session["user_transactions"].append(t)
            new_txns.append(t)
            existing_keys.add(key)
    _db_save_user_transactions(new_txns)
    return new_txns


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    html_path = Path(__file__).parent / "dashboard" / "index.html"
    return html_path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/transactions", methods=["GET"])
def get_transactions():
    """Rescan default folders fresh and merge with manually imported transactions."""
    folder_txns = _scan_default_folders()
    combined = _dedup(folder_txns + _session["user_transactions"])
    return jsonify({"transactions": combined, "count": len(combined)})


@app.route("/import/files", methods=["POST"])
def import_files():
    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "No files provided"}), 400

    all_txns: List[dict] = []
    errors: List[str] = []
    tmp_paths: List[str] = []

    try:
        for f in files:
            suffix = Path(f.filename).suffix.lower()
            if suffix not in {".csv", ".pdf"}:
                errors.append(f"{f.filename}: unsupported type (CSV and PDF only)")
                continue

            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            try:
                f.save(tmp.name)
            finally:
                tmp.close()
            tmp_paths.append(tmp.name)

            label = Path(f.filename).stem
            try:
                result = ingest_file(tmp.name, account_label=label)
                for t in result.transactions:
                    d = t.to_dict()
                    if not d.get("account"):
                        d["account"] = label
                    d["source_folder"] = "expense"  # manual file uploads default to expense
                    all_txns.append(_enrich(d))
                errors.extend(result.errors)
            except Exception as e:
                errors.append(f"{Path(f.filename).name}: parse error — {e}")

    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    masked = _mask_txns(all_txns)
    _merge_into_session(masked)

    try:
        folder_txns = _scan_default_folders()
    except Exception:
        folder_txns = []

    full = _dedup(folder_txns + _session["user_transactions"])
    return jsonify({"transactions": full, "count": len(full), "errors": errors})


@app.route("/import/folder", methods=["POST"])
def import_folder():
    data = request.get_json(force=True, silent=True) or {}
    folder = data.get("folder", "").strip()
    if not folder:
        return jsonify({"error": "folder path required"}), 400

    folder_path = Path(os.path.expanduser(folder))
    if not folder_path.exists():
        return jsonify({"error": f"Folder not found: {folder}"}), 404
    if not folder_path.is_dir():
        return jsonify({"error": f"Not a directory: {folder}"}), 400

    files = sorted(p for p in folder_path.iterdir() if p.suffix.lower() in {".csv", ".pdf"})
    if not files:
        return jsonify({"transactions": [], "count": 0, "files_scanned": [], "errors": ["No CSV or PDF files found"]}), 200

    # Tag as "income" if the folder name contains "income", otherwise "expense"
    source_folder = "income" if "income" in folder_path.name.lower() else "expense"

    all_txns: List[dict] = []
    errors: List[str] = []
    for fp in files:
        label = fp.stem
        result = ingest_file(str(fp), account_label=label)
        for t in result.transactions:
            d = t.to_dict()
            if not d.get("account"):
                d["account"] = label
            d["source_folder"] = source_folder
            all_txns.append(_enrich(d))
        errors.extend(result.errors)

    masked = _mask_txns(all_txns)

    _merge_into_session(masked)

    folder_txns = _scan_default_folders()
    full = _dedup(folder_txns + _session["user_transactions"])
    return jsonify({
        "transactions": full,
        "count": len(full),
        "files_scanned": [f.name for f in files],
        "errors": errors,
    })


@app.route("/check-duplicates", methods=["POST"])
def check_duplicates():
    data = request.get_json(force=True, silent=True) or {}
    new_batch = data.get("new_transactions", [])
    existing = data.get("existing_transactions", [])

    if not new_batch:
        return jsonify({"duplicates": [], "count": 0})

    duplicates = []
    for new_t in new_batch:
        for ex_t in existing:
            same_date     = new_t.get("date") == ex_t.get("date")
            same_amount   = abs(float(new_t.get("amount", 0)) - float(ex_t.get("amount", 0))) < 0.01
            same_merchant = new_t.get("merchant", "").lower().strip() == ex_t.get("merchant", "").lower().strip()
            if same_date and same_amount and same_merchant:
                duplicates.append({
                    "new": new_t,
                    "existing": ex_t,
                    "reason": "exact match (date + amount + merchant)",
                })
                break  # only report one match per new transaction

    return jsonify({"duplicates": duplicates, "count": len(duplicates)})


@app.route("/export/csv", methods=["POST"])
def export_csv():
    data = request.get_json(force=True, silent=True) or {}
    transactions = data.get("transactions", [])
    save_path = data.get("save_path", "").strip()

    if not save_path:
        return jsonify({"error": "save_path required"}), 400
    if not transactions:
        return jsonify({"error": "No transactions to export"}), 400

    out_path = Path(os.path.expanduser(save_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fields = ["date", "merchant", "amount", "category", "account", "source_file"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(transactions)

    return jsonify({"saved_path": str(out_path), "rows": len(transactions)})


@app.route("/export/pdf", methods=["POST"])
def export_pdf():
    data = request.get_json(force=True, silent=True) or {}
    transactions = data.get("transactions", [])
    save_path = data.get("save_path", "").strip()

    if not save_path:
        return jsonify({"error": "save_path required"}), 400
    if not transactions:
        return jsonify({"error": "No transactions to export"}), 400

    out_path = Path(os.path.expanduser(save_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _build_pdf(transactions, str(out_path))
    return jsonify({"saved_path": str(out_path), "rows": len(transactions)})


@app.route("/categories/update", methods=["POST"])
def update_category():
    data = request.get_json(force=True, silent=True) or {}
    txn_id = data.get("txn_id", "").strip()
    new_category = data.get("category", "").strip()

    if not txn_id:
        return jsonify({"error": "txn_id required"}), 400
    if not new_category:
        return jsonify({"error": "category required"}), 400
    all_cats = list(DEFAULT_CATEGORIES) + _session["custom_categories"]
    if new_category not in all_cats:
        return jsonify({"error": f"Unknown category '{new_category}'"}), 400

    _session["categories"][txn_id] = new_category
    _db_save_category(txn_id, new_category)

    merchant = data.get("merchant", "").strip()
    pattern = _extract_pattern(merchant) if merchant else ""
    if pattern:
        _db_save_learned_rule(pattern, new_category)
        _session["learned_rules"] = _db_load_learned_rules()

    return jsonify({"txn_id": txn_id, "category": new_category, "ok": True, "pattern": pattern})


@app.route("/categories/rules", methods=["GET"])
def get_learned_rules():
    return jsonify({"rules": _session["learned_rules"]})


@app.route("/categories/batch-update", methods=["POST"])
def batch_update_categories():
    data = request.get_json(force=True, silent=True) or {}
    updates = data.get("updates", [])
    all_cats = list(DEFAULT_CATEGORIES) + _session["custom_categories"]
    saved = 0
    for u in updates:
        tid = u.get("txn_id", "").strip()
        cat = u.get("category", "").strip()
        if tid and cat and cat in all_cats:
            _session["categories"][tid] = cat
            _db_save_category(tid, cat)
            saved += 1
    return jsonify({"updated": saved, "ok": True})


@app.route("/categories", methods=["GET"])
def get_categories():
    all_cats = list(DEFAULT_CATEGORIES) + [
        c for c in _session["custom_categories"] if c not in DEFAULT_CATEGORIES
    ]
    return jsonify({
        "categories": all_cats,
        "defaults": list(DEFAULT_CATEGORIES),
        "custom": _session["custom_categories"],
    })


@app.route("/categories/add", methods=["POST"])
def add_category():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    all_cats = list(DEFAULT_CATEGORIES) + _session["custom_categories"]
    if name in all_cats:
        return jsonify({"error": f"Category '{name}' already exists"}), 409
    _session["custom_categories"].append(name)
    _db_save_custom_category(name)
    return jsonify({
        "categories": list(DEFAULT_CATEGORIES) + _session["custom_categories"],
        "custom": _session["custom_categories"],
        "added": name,
    })


@app.route("/categories/remove", methods=["POST"])
def remove_category():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    if name in DEFAULT_CATEGORIES:
        return jsonify({"error": f"Cannot remove built-in category '{name}'"}), 400
    if name not in _session["custom_categories"]:
        return jsonify({"error": f"Category '{name}' not found in custom list"}), 404
    _session["custom_categories"].remove(name)
    _db_remove_custom_category(name)
    return jsonify({
        "categories": list(DEFAULT_CATEGORIES) + _session["custom_categories"],
        "custom": _session["custom_categories"],
        "removed": name,
    })


@app.route("/reset", methods=["POST"])
def reset_data():
    """Clear only the in-memory session (imported transactions this run).
    SQLite data (learned rules, category overrides) is preserved."""
    _session["user_transactions"].clear()
    return jsonify({"ok": True})


@app.route("/reset/full", methods=["POST"])
def reset_full():
    """Clear everything: in-memory session + all SQLite persistent data.
    Original CSV/PDF files are never touched.
    """
    with _db_conn() as conn:
        conn.execute("DELETE FROM user_transactions")
        conn.execute("DELETE FROM category_overrides")
        conn.execute("DELETE FROM learned_rules")
    _session["user_transactions"].clear()
    _session["categories"].clear()
    _session["learned_rules"] = []
    return jsonify({"ok": True})


# ── PDF Builder ───────────────────────────────────────────────────────────────

def _build_pdf(transactions: List[dict], out_path: str) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, HRFlowable,
    )

    W, H = letter
    doc = SimpleDocTemplate(
        out_path, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title2", parent=styles["Title"], fontSize=18, spaceAfter=4, textColor=colors.HexColor("#1e1b4b")
    )
    h2_style = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=11, spaceBefore=14, spaceAfter=6,
        textColor=colors.HexColor("#3730a3"),
    )
    small_style = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, textColor=colors.gray)

    # ── Compute stats ──────────────────────────────────────────────────────────
    dates = sorted(t.get("date", "") for t in transactions if t.get("date"))
    date_range = f"{dates[0]} → {dates[-1]}" if len(dates) >= 2 else (dates[0] if dates else "N/A")
    export_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    expense_txns = [t for t in transactions if t.get("source_folder", "expense") != "income"]
    income_txns  = [t for t in transactions if t.get("source_folder", "expense") == "income"]
    # Spending = net outflow from expense folder: debits (negative amounts) minus credits (positive amounts)
    total_spent  = -sum(float(t.get("amount", 0)) for t in expense_txns)
    total_income = sum(float(t.get("amount", 0)) for t in income_txns)
    net = total_income - total_spent

    cat_totals: Dict[str, float] = {}
    for t in expense_txns:
        cat = t.get("category", "Uncategorized")
        cat_totals[cat] = cat_totals.get(cat, 0) + abs(float(t.get("amount", 0)))
    cat_sorted = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)

    # ── Shared table style factories ──────────────────────────────────────────
    HDR_BG     = colors.HexColor("#1e1b4b")
    HDR_BG2    = colors.HexColor("#3730a3")
    ROW_STRIPE = [colors.HexColor("#f8fafc"), colors.white]
    GRID_COL   = colors.HexColor("#e2e8f0")

    def base_style(header_color=HDR_BG) -> list:
        return [
            ("BACKGROUND",  (0, 0), (-1, 0), header_color),
            ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME",    (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE",    (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), ROW_STRIPE),
            ("GRID",        (0, 0), (-1, -1), 0.4, GRID_COL),
            ("PADDING",     (0, 0), (-1, -1), 5),
            ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ]

    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph("Expense Summary Report", title_style))
    story.append(Paragraph(f"Period: {date_range}", styles["Normal"]))
    story.append(Paragraph(f"Exported: {export_ts}", small_style))
    story.append(Spacer(1, 0.12 * inch))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))
    story.append(Spacer(1, 0.08 * inch))

    # ── Summary table ─────────────────────────────────────────────────────────
    story.append(Paragraph("Summary", h2_style))
    summary_data = [
        ["Metric", "Value"],
        ["Total Spent",    f"${total_spent:,.2f}"],
        ["Total Income",   f"${total_income:,.2f}"],
        ["Net",            f"${net:+,.2f}"],
        ["Transactions",   str(len(transactions))],
    ]
    tbl = Table(summary_data, colWidths=[2.8 * inch, 2 * inch])
    tbl.setStyle(TableStyle(base_style(HDR_BG)))
    story.append(tbl)
    story.append(Spacer(1, 0.1 * inch))

    # ── Category breakdown ────────────────────────────────────────────────────
    if cat_sorted:
        story.append(Paragraph("Category Breakdown", h2_style))
        cat_data = [["Category", "Transactions", "Total", "% of Spend"]]
        for cat, amt in cat_sorted:
            count = sum(1 for t in expense_txns if t.get("category") == cat)
            pct = (amt / total_spent * 100) if total_spent else 0
            cat_data.append([cat, str(count), f"${amt:,.2f}", f"{pct:.1f}%"])
        style = base_style(HDR_BG2)
        style.append(("ALIGN", (1, 0), (-1, -1), "RIGHT"))
        cat_tbl = Table(cat_data, colWidths=[2.5 * inch, 1.2 * inch, 1.4 * inch, 1.1 * inch])
        cat_tbl.setStyle(TableStyle(style))
        story.append(cat_tbl)
        story.append(Spacer(1, 0.1 * inch))

    # ── Full transaction list ─────────────────────────────────────────────────
    story.append(Paragraph("All Transactions", h2_style))
    txn_header = [["Date", "Merchant", "Amount", "Category", "Source"]]
    txn_rows = []
    for t in sorted(transactions, key=lambda x: x.get("date", ""), reverse=True):
        amt = float(t.get("amount", 0))
        merchant = mask_sensitive(t.get("merchant", ""))
        merchant = (merchant[:32] + "…") if len(merchant) > 33 else merchant
        source = t.get("source_file", "")
        source = (source[:18] + "…") if len(source) > 19 else source
        txn_rows.append([
            t.get("date", ""),
            merchant,
            f"${abs(amt):,.2f}",
            t.get("category", ""),
            source,
        ])
    txn_data = txn_header + txn_rows
    txn_style = base_style(colors.HexColor("#0f172a"))
    txn_style.append(("FONTSIZE", (0, 0), (-1, -1), 8))
    txn_style.append(("ALIGN",   (2, 0), (2, -1), "RIGHT"))
    txn_tbl = Table(txn_data, colWidths=[0.85 * inch, 2.35 * inch, 0.9 * inch, 1.4 * inch, 1.2 * inch])
    txn_tbl.setStyle(TableStyle(txn_style))
    story.append(txn_tbl)

    # ── Page footer ───────────────────────────────────────────────────────────
    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.gray)
        canvas.drawString(0.75 * inch, 0.4 * inch, "Generated locally — data never left this device")
        canvas.drawRightString(W - 0.75 * inch, 0.4 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


# ── Chat (llama.cpp / local LLM) ─────────────────────────────────────────────

LLAMA_CPP_URL = os.environ.get("LLAMA_CPP_URL", "http://localhost:8080")


@app.route("/chat/status", methods=["GET"])
def chat_status():
    """Check whether llama-server is reachable."""
    import requests as req
    try:
        resp = req.get(f"{LLAMA_CPP_URL}/health", timeout=2)
        if resp.status_code == 200:
            return jsonify({"available": True})
    except Exception:
        pass
    return jsonify({"available": False})


@app.route("/chat/config", methods=["GET"])
def chat_config():
    """
    Return chat context configuration shared between frontend and backend.
    Frontend uses these values to build the context it sends to /chat.
    """
    return jsonify({
        "maxTrendMonths": 36,        # cap for monthly trend history (3 years); frontend computes actual span dynamically
    })


@app.route("/chat", methods=["POST"])
def chat():
    """
    Stream a response from llama-server given a user message and conversation history.

    Request JSON:
        message  (str)  — latest user message
        history  (list) — prior turns: [{role: "user"|"assistant", content: "..."}]
        data     (dict) — frontend-provided context:
            - monthlyTrend: [{month, expenses, income, net, categories, merchants}]
            - categories: [category names]

    Response: text/plain stream of assistant tokens.
    The final token(s) may contain [ACTION: {...}] — a structured command
    the frontend can parse to update the dashboard state.
    """
    import requests as req

    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    user_data = data.get("data") or {}

    if not message:
        return jsonify({"error": "message required"}), 400

    # Extract frontend-provided context
    monthly_trend = user_data.get("monthlyTrend", [])
    categories = user_data.get("categories", [])

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Format helpers
    def _fmt_month(m: dict) -> str:
        lines = [f"  {m['month']}  expenses: ${m['expenses']:>8,.2f}  income: ${m['income']:>8,.2f}  net: ${m['net']:>+9,.2f}"]
        cats = m.get("categories", [])
        merchs = m.get("merchants", [])
        if cats:
            lines.append("    Categories: " + ", ".join(f"{c['category']} ${c['total']:,.2f}" for c in cats))
        if merchs:
            lines.append("    Top merchants: " + ", ".join(f"{x['merchant']} ${x['total']:,.2f}" for x in merchs))
        return "\n".join(lines)

    trend_block = "\n".join(_fmt_month(m) for m in monthly_trend) or "  (no data)"

    system_prompt = f"""You are a personal finance assistant. The user's expense data is stored locally on their machine — all analysis is private.

Today: {today_str}

MONTHLY DATA (last {len(monthly_trend)} months — totals, category breakdown, top merchants per month):
{trend_block}

AVAILABLE CATEGORIES: {", ".join(categories)}

Guidelines:
- Be concise. Use real numbers from the data above.
- When the user asks about a time period, sum or filter the monthly data above accordingly.
- If the user asks to filter, show, or navigate the dashboard, append exactly one ACTION block at the very end of your response (after your text).
- ACTION block format (JSON, no extra text after it):
  [ACTION: {{"type":"filter","period":"lastmonth"}}]
  [ACTION: {{"type":"filter","category":"Food & Dining"}}]
  [ACTION: {{"type":"export","format":"pdf"}}]
- Only emit an ACTION when the user explicitly requests a dashboard change or export.
- Never emit more than one ACTION block."""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-10:])  # keep last 10 turns for context window
    messages.append({"role": "user", "content": message})

    # ── Stream from llama-server ──────────────────────────────────────────────
    def generate():
        try:
            resp = req.post(
                f"{LLAMA_CPP_URL}/v1/chat/completions",
                json={
                    "messages": messages,
                    "stream": True,
                    "temperature": 0.3,
                    "max_tokens": 512,
                },
                stream=True,
                timeout=120,
            )
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if line.startswith("data: "):
                    payload = line[6:]
                elif line.startswith("data:"):
                    payload = line[5:]
                else:
                    continue
                if payload.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                    delta = obj["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except (KeyError, json.JSONDecodeError):
                    pass
        except req.exceptions.ConnectionError:
            yield "\n[AI Offline — start llama-server and try again]"
        except Exception as e:
            yield f"\n[Error: {e}]"

    return Response(
        stream_with_context(generate()),
        content_type="text/plain; charset=utf-8",
    )


# ── Entry point ───────────────────────────────────────────────────────────────

_init_db()
_load_session_from_db()

if __name__ == "__main__":
    print("Expense Tracker running at http://127.0.0.1:5050")
    print(f"State DB: {_get_db_path()}")
    print("All data stays local. No external connections.")
    app.run(host="127.0.0.1", port=5050, debug=False)
