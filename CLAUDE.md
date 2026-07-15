# CLAUDE.md

This file provides guidance to AI agents (Claude Code, Kiro, Cursor, and others) working in this repository.

## Commands

**Run the app:**
```bash
finn-tracker
# Opens http://localhost:5050 automatically
```

**Run tests**:

> Before running any tests or Python commands, ask the user which Python environment to use. Do not probe the filesystem to discover it. Example: "Which Python environment should I use to run tests? (e.g. the repo's uv-managed `.venv`, or another interpreter path)"
>
> This repo uses [uv](https://docs.astral.sh/uv/) for environments. If none exists yet: `uv venv --python 3.12 && uv pip install -r requirements.txt -e ".[dev]"` — the test binary is then `.venv/bin/python`.

```bash
# Use the Python binary from the environment the user specifies, e.g.:
<python> -m pytest tests/ -v

# Single test file:
<python> -m pytest tests/test_app.py -v

# Single test class:
<python> -m pytest tests/test_app.py::TestPersistence -v

# Single test:
<python> -m pytest tests/test_app.py::TestPersistence::test_db_init_creates_tables -v
```

All 495 tests live in `tests/`:
- `test_app.py` — parsers, Flask routes, persistence, AI chat
- `test_cli.py` — CLI, packaging
- `test_db.py` — shared data access layer, analytics, period filtering
- `test_ingest.py` — file routing, multi-file ingestion
- `test_pdf_parser.py` — PDF parsing, account detection, table/text extraction

## Architecture

```
finn_tracker/
  app.py            Flask backend + SQLite persistence layer + /chat endpoint
  ingest.py         Routes .csv/.pdf files to the right parser
  models.py         Transaction dataclass, ParseResult, mask_sensitive(), DEFAULT_CATEGORIES, autocat()
  mcp_server.py     MCP server (stdio) for Claude Desktop / Claude Code / Cursor / Kiro
  utils/
    db.py           Shared data-access layer (no Flask import) — used by app.py and mcp_server.py
  parsers/
    csv_parser.py   Auto-detects Chase Bank, Chase Credit, BofA, Capital One, or generic CSV
    pdf_parser.py   Table + text-fallback extraction (pdfplumber); posting-date aware
  dashboard/
    index.html      Entire frontend — vanilla JS, no build step, ~2000 lines
sample_data/
  generators.py     Synthetic CSV/PDF fixtures for tests and --demo mode (no real bank data)
tests/
  test_app.py       Parsers, Flask routes, persistence, privacy masking, AI chat
  test_cli.py       CLI entry point, packaging, data directory setup
  test_db.py        Shared data access layer, analytics, period filtering
  test_ingest.py    File routing, multi-file ingestion
  test_pdf_parser.py  PDF parsing, account detection, table/text extraction
~/Documents/finn-tracker/         ← default; override with EXPENSE_TRACKER_DATA
  expense/          Auto-loaded CSVs/PDFs on every GET /transactions (expense folder)
  income/           Auto-loaded CSVs/PDFs on every GET /transactions (income folder)
  finn_tracker.db   SQLite DB (gitignored); created on first run
```

### Data flow

1. `GET /transactions` → `_scan_default_folders()` scans files in `expense/` and `income/` using a per-file mtime cache (`_scan_cache` in `utils/db.py`) — unchanged files are skipped. Results merged with `_session["user_transactions"]` (manually imported files), deduplicated by `(date, merchant, amount, account)`, enriched with category overrides and learned rules, and returned as JSON.
2. `POST /import/files` or `/import/folder` → files parsed, merged into `_session["user_transactions"]`, and written to the `user_transactions` SQLite table.
3. On startup: `_init_db()` creates tables, `_load_session_from_db()` restores `_session` from DB.
4. **AI Chat (frontend-first architecture)**:
   - `GET /chat/config` → returns shared configuration (`maxTrendMonths`, `dbPath`)
   - Frontend fetches config on page load, computes aggregates (top merchants, category totals, monthly trend) from loaded transactions
   - `POST /chat` → frontend sends pre-computed context + user message; backend formats system prompt from provided data (no DB query), streams response from llama-server at `LLAMA_CPP_URL` (default `http://localhost:8080`)
   - Benefits: single source of truth (what user sees = what LLM sees), no duplicate DB queries, easy to tune context window

### utils/db.py

Shared query layer with no Flask dependency. `app.py` imports folder-scanning and pattern-extraction helpers; `mcp_server.py` imports all query functions. Key functions:

| Function | Description |
|---|---|
| `get_all_transactions(db_path)` | Scan folders + load user imports + dedup + apply overrides |
| `get_spending_summary(txns, period)` | Category totals + percentages |
| `get_top_merchants(txns, period, limit)` | Top N by spend |
| `get_monthly_trend(txns, months)` | Month-by-month totals |
| `filter_by_period(txns, period)` | Supports: 1m, 3m, 6m, ytd, thismonth, lastmonth, all, custom |

### MCP server

`mcp_server.py` exposes expense data as an MCP server (stdio transport). Used by Claude Desktop, Claude Code, Cursor, Kiro, and other MCP-compatible clients. Runs natively — never inside the Flask process.

Claude Desktop config (`/path_to_claude_installation/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "finn-tracker": {
      "command": "/path/to/your/python",
      "args": ["/path/to/finn-tracker/finn_tracker/mcp_server.py"]
    }
  }
}
```

### Sign convention

`amount` is **negative for charges/debits, positive for credits/payments**. This is consistent throughout parsers, models, and frontend.

Frontend `normalize(t)` computes:
- `spending = type === "expense" ? -rawAmount : 0` — net outflow contribution
- `abs = Math.abs(rawAmount)` — for display

### Transaction type system (frontend only)

`normalize()` in `index.html` assigns `type` in this priority order:
1. `"payment"` — credit (positive amount) + merchant matches one of `AUTOPAY_PATTERNS` (BofA ONLINE/MOBILE RECURRING, Chase AUTOMATIC PAYMENT - THANK, Capital One CAPITAL ONE AUTOPAY PYMT)
2. `"transfer"` — keywords like "transfer", "zelle"
3. `"income"` — `source_folder === "income"`
4. `"expense"` — everything else

`type` is frontend-only; it is NOT stored in the DB or returned by the API. All expense calculations filter on `t.type === "expense"`, so payments are automatically excluded.

### SQLite persistence

Four tables in `~/Documents/finn-tracker/finn_tracker.db`:

| Table | Key | What's stored |
|---|---|---|
| `category_overrides` | `txn_id` | User category edits (survive restarts) |
| `custom_categories` | `id` | User-added category names |
| `user_transactions` | `txn_id` | Manually imported transactions (JSON blob) |
| `learned_rules` | `pattern` | Merchant-pattern → category rules (auto-propagation) |

Folder-scanned transactions (`expense/`, `income/`) are **not** stored in the DB — they are re-scanned on every request, but an in-memory mtime cache (`_scan_cache` in `utils/db.py`) skips unchanged files within a server process lifetime.

### Category learning system

When a user assigns a category to a transaction, `POST /categories/update` also extracts a merchant pattern via `_extract_pattern(merchant)` in `app.py` and saves it to `learned_rules`. Future transactions whose normalized merchant matches the pattern are auto-categorized without a user override.

**`_extract_pattern()` normalization pipeline** (Python and JS `normalizeMerchant()` must stay in sync):
1. Strip POS prefixes: `SQ *`, `TST*`, `PP*`, `SP `
2. Strip everything after `-` or `–` (location qualifiers)
3. Remove `.` and `*`
4. Remove `#\d*` tokens (store numbers like `#338`)
5. Remove digits glued to letters (`BESTBUYCOM807...` → `BESTBUYCOM`)
6. Remove remaining standalone 4+ digit sequences
7. Collapse whitespace; take first 2 tokens

**Rule application priority — server then frontend:**
1. Server: explicit category override from `category_overrides` table — highest
2. Server: learned rule match from `learned_rules` table
3. Server: `autocat()` static regex rules (200+ patterns)
4. Frontend: in-memory learned rule (catches newly created rules before next page load)
5. "Uncategorized"

The server applies steps 1–3 in `_enrich()` before returning any transaction. The frontend `RULES = []` — autocat runs server-side only.

`POST /categories/batch-update` is a fire-and-forget endpoint used when a new learned rule propagates to similar transactions — it does NOT create new rules (avoids cascading bad rules).

`txn_id` is a stable 12-char MD5 hash of `(date, merchant, amount, account)` — computed by `make_txn_id()` in `utils/db.py` and imported by `app.py`.

### Privacy

`mask_sensitive()` in `models.py` masks 16-digit card numbers, 8–12 digit account numbers, and SSNs. It is applied before any JSON response leaves the server and again in the PDF export builder. The server binds to `127.0.0.1` only.

The `/chat` endpoint connects to `LLAMA_CPP_URL` (default `http://localhost:8080`) — a locally-running llama-server. No transaction data is sent to any external service. Set `LLAMA_CPP_URL` in the environment to point to a different local port if needed.

---

## Agent guidelines

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

### 5. No System Paths in Docs or Code

**Never hardcode absolute system paths (e.g. `/Users/username/...`, `C:\Users\...`) in any documentation, comments, or committed code.**

This is a public repository. Use generic placeholders like `<python>`, `~/`, or relative paths instead.

### 6. Never Embed Real Financial Data

**STRICT: Never use real account numbers, card numbers, card last-4 digits, or real dollar totals from the user's actual bank statements in any of the following:**
- Test fixtures or sample data generators
- Code comments or inline examples
- Commit messages
- PR titles or descriptions
- Any file tracked by git

**This applies even when the user shares a real PDF or CSV for reference.** Filenames like `Chase_1234_statement.pdf` or `savor_04_2026_5678.pdf` reveal real last-4 digits — do not carry those numbers into code or commits.

**Always use clearly fake placeholder numbers** (e.g. `1234`, `5678`, `9012`) in all generated fixtures and tests. The numbers must not match any pattern visible in files the user has shared.

Violation requires a full git history rewrite and may expose the user's financial account identifiers publicly.

---

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
