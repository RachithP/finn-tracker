"""
mcp_server.py — MCP server for the expense tracker.

Exposes tools and resources so MCP-compatible clients (Claude Desktop, Claude Code,
Cursor, Kiro, etc.) can query your local expense data without the web app running.

Usage:
    python mcp_server.py

Claude Desktop config  (~/.../claude_desktop_config.json):
    {
      "mcpServers": {
        "finn-tracker": {
          "command": "/path/to/your/python",
          "args": ["/path/to/finn-tracker/mcp_server.py"]
        }
      }
    }

Claude Code / other clients: add the same block to their respective config files.
Toggle on/off via Claude Desktop → Settings → MCP Servers.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Resolve data dir before any utils.db import so _get_default_folders() picks it up.
_data_dir = Path(os.environ.get("EXPENSE_TRACKER_DATA", Path.home() / "Documents" / "finn-tracker"))
os.environ.setdefault("EXPENSE_TRACKER_DATA", str(_data_dir))
DB_PATH = _data_dir / "finn_tracker.db"

from mcp.server.fastmcp import FastMCP

from utils.db import (
    dedup,
    filter_by_period,
    get_all_transactions,
    get_categories,
    get_monthly_trend,
    get_spending_summary,
    get_top_merchants,
    load_learned_rules,
    save_category_override,
)

# ── Server definition ─────────────────────────────────────────────────────────

mcp = FastMCP(
    "finn-tracker",
    instructions=(
        "Access and analyze personal expense data stored locally on this machine. "
        "All data is private — never stored remotely. "
        "Negative amounts = charges/debits (expenses). "
        "Positive amounts = credits/payments/income. "
        "Use get_spending_summary to answer spending questions before listing raw transactions."
    ),
)

# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
def get_transactions(
    period: str = "all",
    category: str = "",
    account: str = "",
    limit: int = 100,
) -> str:
    """
    Get a filtered list of transactions.

    Args:
        period:   Time window — one of: thismonth, lastmonth, 1m, 3m, 6m, ytd, all.
        category: Filter by exact category name (e.g. "Food & Dining"). Empty = all.
        account:  Filter by account name (partial, case-insensitive). Empty = all.
        limit:    Maximum rows to return (default 100).

    Returns JSON array. Each item has: date, merchant, amount, category, account,
    source_folder, txn_id.
    """
    txns = get_all_transactions(DB_PATH)
    txns = filter_by_period(txns, period)

    if category:
        txns = [t for t in txns if t.get("category", "").lower() == category.lower()]
    if account:
        txns = [t for t in txns if account.lower() in t.get("account", "").lower()]

    txns_sorted = sorted(txns, key=lambda t: t.get("date", ""), reverse=True)
    return json.dumps(txns_sorted[:limit], indent=2)


@mcp.tool()
def get_spending_summary_tool(period: str = "thismonth") -> str:
    """
    Get spending totals broken down by category for a given period.

    Args:
        period: One of: thismonth, lastmonth, 1m, 3m, 6m, ytd, all.

    Returns JSON with:
      - total: total dollars spent
      - count: number of expense transactions
      - by_category: list of {category, total, count, pct} sorted by total desc
    """
    txns = get_all_transactions(DB_PATH)
    summary = get_spending_summary(txns, period)
    return json.dumps(summary, indent=2)


@mcp.tool()
def get_top_merchants_tool(period: str = "thismonth", limit: int = 10) -> str:
    """
    Get the top merchants ranked by total spending.

    Args:
        period: One of: thismonth, lastmonth, 1m, 3m, 6m, ytd, all.
        limit:  Number of merchants to return (default 10).

    Returns JSON array of {merchant, total, count} sorted by total desc.
    """
    txns = get_all_transactions(DB_PATH)
    merchants = get_top_merchants(txns, period, limit)
    return json.dumps(merchants, indent=2)


@mcp.tool()
def get_monthly_trend_tool(months: int = 6) -> str:
    """
    Get month-by-month spending and income totals.

    Args:
        months: How many months to look back (default 6).

    Returns JSON array of {month (YYYY-MM), expenses, income, net}
    sorted oldest → newest.
    """
    txns = get_all_transactions(DB_PATH)
    trend = get_monthly_trend(txns, months)
    return json.dumps(trend, indent=2)


@mcp.tool()
def update_category(txn_id: str, category: str) -> str:
    """
    Update the category for a specific transaction (persisted to DB).

    Args:
        txn_id:   12-character transaction ID (from get_transactions results).
        category: New category name. Must be a valid category (see get_categories_tool).

    Returns JSON confirmation.
    """
    valid_cats = get_categories(DB_PATH)
    if category not in valid_cats:
        return json.dumps({
            "ok": False,
            "error": f"Unknown category '{category}'",
            "valid_categories": valid_cats,
        })
    save_category_override(txn_id, category, DB_PATH)
    return json.dumps({"ok": True, "txn_id": txn_id, "category": category})


@mcp.tool()
def get_categories_tool() -> str:
    """
    Get all available categories (built-in defaults + user-added custom ones).

    Returns JSON array of category name strings.
    """
    return json.dumps(get_categories(DB_PATH), indent=2)


# ── Resources ─────────────────────────────────────────────────────────────────


@mcp.resource("expenses://summary")
def summary_resource() -> str:
    """
    Always-fresh snapshot: current-month spending + YTD totals.
    Read this first to orient yourself before answering spending questions.
    """
    txns = get_all_transactions(DB_PATH)
    return json.dumps(
        {
            "this_month": get_spending_summary(txns, "thismonth"),
            "ytd": get_spending_summary(txns, "ytd"),
            "top_merchants_this_month": get_top_merchants(txns, "thismonth", 5),
        },
        indent=2,
    )


@mcp.resource("expenses://categories")
def categories_resource() -> str:
    """Full category list and learned merchant-pattern → category rules."""
    return json.dumps(
        {
            "categories": get_categories(DB_PATH),
            "learned_rules": load_learned_rules(DB_PATH),
        },
        indent=2,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
