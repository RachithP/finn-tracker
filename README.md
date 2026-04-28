# finn-tracker

[![CI](https://github.com/RachithP/finn-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/RachithP/finn-tracker/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/RachithP/9088e1501976e0528e0da2fa5d38a465/raw/badge.json)](https://github.com/RachithP/finn-tracker/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A fully local expense tracking and visualization tool. Import bank CSVs and PDF statements, auto-categorize transactions, and explore spending trends through an interactive dashboard. **No data ever leaves your machine.**

---

## Features

- **Auto-import** — drop CSVs/PDFs into `~/Documents/finn-tracker/expense/` (or `income/`) and they load automatically on every page refresh
- **Smart categorization** — static rules auto-categorize common merchants; manual overrides are persisted and learned
- **Interactive dashboard** — summary cards, spending-by-category bar chart, account donut chart, spending trend timeline, and category drill-down
- **Period filtering** — 1M, 3M, 6M, YTD, This Month, Last Month, All, or a custom date range
- **Export** — CSV or PDF report with masked merchant names
- **AI chat assistant** — ask questions about your spending in plain English ("How much did I spend on food last month?"). Powered by a local LLM ([llama.cpp](https://github.com/ggerganov/llama.cpp)) — your data never leaves your machine
- **MCP server** — connect Claude Desktop, Cursor, Kiro, and other AI tools directly to your expense data via the [Model Context Protocol](https://modelcontextprotocol.io)
- **Privacy-first** — server binds to `127.0.0.1` only; all state stored in a local SQLite DB; sensitive strings masked before any API response

---

## Quick Start

### Step 1 — Install Python (if you haven't already)

finn-tracker requires Python 3.9 or later. Check if you have it:

```bash
python3 --version
```

If you see `Python 3.9` or higher, skip to Step 2. Otherwise, install it:

- **macOS**: [Download from python.org](https://python.org/downloads) or run `brew install python`
- **Ubuntu/Debian**: `sudo apt install python3`

> **Note:** finn-tracker is developed and tested on macOS and Ubuntu. It may work on other platforms but is not officially supported on Windows.

### Step 2 — Install finn-tracker

Open a terminal and run:

```bash
pip install finn-tracker
```

> **Tip:** If `pip` isn't found, try `pip3 install finn-tracker` or `python3 -m pip install finn-tracker`.

> **Optional — use a virtual environment:** If you want to keep finn-tracker isolated from other Python packages, create a virtual environment first:
> ```bash
> python3 -m venv ~/.venvs/finn-tracker
> source ~/.venvs/finn-tracker/bin/activate
> pip install finn-tracker
> ```
> You'll need to activate the environment (`source ~/.venvs/finn-tracker/bin/activate`) each time before running `finn-tracker`.

### Step 3 — Launch

```bash
finn-tracker
```

Your browser opens automatically at `http://localhost:5050`.

### Step 4 — Add your bank statements

Drop your bank CSV or PDF exports into:

```
~/Documents/finn-tracker/expense/   ← charges, debits
~/Documents/finn-tracker/income/    ← salary, deposits
```

Then refresh the page — your transactions appear automatically.

> **Not sure where to find those folders?**
> - **macOS**: Open Finder, press **⌘ Shift H** to go to your home folder, then open `Documents → finn-tracker`.
> - **Linux**: Open your file manager and navigate to `~/Documents/finn-tracker/`.

---

### Try it first with sample data

Not ready to import real statements yet? Run this to load synthetic demo data:

```bash
finn-tracker --demo
```

---

## Privacy Guarantee

No data leaves your machine. finn-tracker:

- Runs at `127.0.0.1:5050` — not accessible from the network by default
- Stores everything in SQLite on your disk (`~/Documents/finn-tracker/finn_tracker.db`)
- Never makes outbound network calls
- Deletes uploaded files immediately after parsing
- Masks card numbers, SSNs, and account numbers in all API responses

---

## AI Chat Assistant

finn-tracker includes a built-in chat assistant that answers questions about your spending in plain English:

> "How much did I spend on groceries last month?"
> "What's my biggest expense category this year?"
> "Show me my top 5 merchants"
> "Filter the dashboard to last month"
> "Which transactions are uncategorized?"

The assistant can answer spending questions and control the dashboard — filtering by period or category on your behalf. It runs entirely on your machine using [llama.cpp](https://github.com/ggerganov/llama.cpp). No data is sent to any external service.

**To enable it:**

1. Install and start [llama-server](https://github.com/ggerganov/llama.cpp#quick-start) on port 8080 (the default)
2. Launch finn-tracker — the chat button in the top-right corner will show **AI Ready**

To use a different port: `LLAMA_CPP_URL=http://localhost:8081 finn-tracker`

---

## MCP Server (Claude Desktop, Cursor, Kiro)

finn-tracker ships an [MCP server](https://modelcontextprotocol.io) that lets AI tools query your expense data directly — no browser required.

**To connect Claude Desktop:**

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "finn-tracker": {
      "command": "/path/to/your/python",
      "args": ["/path/to/finn-tracker/mcp_server.py"]
    }
  }
}
```

Once connected, you can ask Claude things like "summarize my spending this month" or "what did I spend on dining last quarter" directly in Claude Desktop.

---

## Supported File Formats

| Format | Auto-detected banks |
|---|---|
| CSV | Chase Bank (checking), Chase Credit, Bank of America, Capital One, generic |
| PDF | Table-based and text-fallback extraction (pdfplumber) |

---

## How It Works

### Data flow
1. Files in `~/Documents/finn-tracker/expense/` and `income/` are parsed on every `GET /transactions` request.
2. Manually imported files (via the dashboard) are parsed once and persisted to SQLite.
3. All transactions are deduplicated by a stable hash of `(date, merchant, amount, account)`.
4. Category overrides and learned merchant rules survive server restarts via SQLite.

### Category learning
When you manually categorize a transaction, the app extracts a normalized merchant pattern and saves it as a rule. Future transactions matching that pattern are auto-categorized.

### Sign convention
`amount` is **negative for charges/debits, positive for credits/payments** — consistent throughout parsers and the frontend.

### Resetting your data

Two buttons in the top-right corner of the dashboard let you clear data at different scopes:

| Button | What it clears | What it keeps |
|---|---|---|
| **🗑 Clear Session** | Transactions imported during the current run (via the dashboard's import button) | Auto-scan folders, SQLite data, learned rules, category overrides |
| **🗑 Clear All** | Everything in SQLite — all transactions, category overrides, and learned merchant rules | Your original CSV/PDF files (they reload from auto-scan folders on next refresh) |

Use **Clear Session** to undo a bad import without losing your history. Use **Clear All** to start completely fresh. Both actions ask for confirmation before proceeding.

---

## Running Tests

```bash
pip install finn-tracker[dev]
python -m pytest tests/ -v
```

324 tests across five files:
- `tests/test_app.py` — parsers, Flask routes, persistence, privacy masking, AI chat endpoints
- `tests/test_cli.py` — CLI entry point, packaging, data directory setup
- `tests/test_db.py` — shared data access layer, analytics, period filtering, folder scanning
- `tests/test_ingest.py` — file routing, multi-file ingestion, merge, summary
- `tests/test_pdf_parser.py` — PDF statement parsing, account detection, table/text extraction

---

## Platform Support

finn-tracker is developed and tested on **macOS** and **Ubuntu Linux**. CI runs on both platforms across Python 3.9, 3.11, and 3.12. Other Unix-like systems should work but are not officially tested. Windows is not supported.

---

## Contributing

Found a bug or want to add a bank parser? See [CONTRIBUTING.md](CONTRIBUTING.md) for how to get started.

For performance optimization guidance when scaling beyond 10K transactions, see [SCALING.md](SCALING.md).

[Open an issue on GitHub](https://github.com/RachithP/finn-tracker/issues) — include the output of `finn-tracker --version`.
