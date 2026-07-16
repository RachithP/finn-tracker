# Changelog

All notable changes to finn-tracker are documented here.

## [Unreleased]

### Privacy

- `mask_sensitive()` now catches Amex 15-digit and all 13-19 digit card numbers (previously only 16-digit cards and 8-12 digit accounts), and masks digit runs glued to letters/underscores (e.g. `stmt_123456789012.csv`) that word-boundary matching missed
- `account` and `source_file` are masked in every API response (`Transaction.to_dict()`); bank-style last-4 labels like `Chase ••1234` stay readable
- MCP server no longer returns `source_file` — statement filenames never leave the machine (`get_transactions` output is now a strict field whitelist)
- Import endpoints mask error messages and `files_scanned` filenames before responding
- CSV and PDF exports re-mask merchant, account, and source filename on write (defense in depth)
- Transactions loaded from the SQLite `user_transactions` table are now re-masked on every read (both the Flask session loader and the shared MCP/analytics query layer) — closes a gap where rows persisted before masking coverage existed would still leak account/source_file
- `mask_sensitive()` now catches account numbers grouped in exact 4-digit chunks (e.g. `1234-5678-9012`), narrowly scoped so it can't false-positive on dates

### Changed

- Removed unreachable dashboard JavaScript (4 dead functions — one referencing a DOM element that no longer existed — plus an unused variable) and unified HTML escaping behind a single `_escHtml()` helper
- Consolidated duplicated code: dollar-amount parsing (`csv_parser.py`/`pdf_parser.py` merged into `models.parse_amount()`), the `/import/files`/`/import/folder` per-file ingest loop (`app.py`), the category-sort comparator, and the timeline/category-trend chart SVG axis rendering (`index.html`) — no behavior change
- Tooling migrated from conda to uv: install/dev docs, CI workflows, and packaging (`environment.yml` removed; `mcp` added to dev extras on Python ≥3.10)
- Dependency management now uses a committed `uv.lock` instead of `requirements.txt` — CI and dev setup run `uv sync --extra dev` for fully reproducible installs across the supported Python range; `requirements.txt` removed

### Categorization

- Added Education and Fitness categories — Education covers Udemy, edX, Khan Academy, Coursera, Duolingo, LeetCode, Chegg, and tuition/student-loan keywords; Fitness covers gyms, yoga/pilates/barre studios, Peloton, and climbing gyms (previously lumped into Health & Medical or Entertainment)

## [0.0.1] - 2026-05-13

Initial release.

### Core

- Fully local expense tracker — no data ever leaves your machine
- Import bank CSVs and PDFs, auto-categorize transactions, explore spending via an interactive dashboard
- SQLite persistence with stable deduplication by `(date, merchant, amount, account)` hash
- Privacy-first: server binds to `127.0.0.1`, sensitive strings masked in all API responses

### Dashboard

- Summary cards, spending-by-category bar chart, account donut chart, spending trend timeline, category drill-down
- Period filtering: 1M, 3M, 6M, YTD, This Month, Last Month, All, custom date range
- Dark theme (default) with light theme toggle
- CSV and PDF export with masked merchant names
- Empty-state onboarding card for first-time users
- Clear Session / Clear All buttons for resetting data at different scopes

### Parsers

- CSV auto-detection for Chase Bank (checking), Chase Credit, Bank of America, Capital One, and generic formats
- CSV title-row detection — bank-exported CSVs with a filename or summary row before the column headers (Chase, Capital One) parse correctly; scans up to 5 rows to find the real header
- PDF support for Capital One, Chase, and Bank of America (Visa Signature) credit card statements; handles short-form dates and inverted charge signs automatically
- PDF posting date — reads column headers (`Post Date`, `Posting Date`, `Posted Date`) to select the correct date column; handles split two-line headers (BofA format)
- PDF table extraction with text-fallback (pdfplumber)
- Summary line filtering prevents statement totals from being parsed as transactions
- Deduplication key includes `account` — the same charge on two different cards is no longer collapsed into one transaction

### Categorization

- 200+ static rules across 15 categories covering grocery chains, restaurants, gas stations, streaming services, airlines, hotels, insurance, government fees, and more
- Donations — new built-in category for GoFundMe, Red Cross, Wikipedia, and similar
- Manual overrides persisted and learned as reusable merchant-pattern rules
- Learned rules applied server-side on every transaction fetch
- Learned rule save notification shows which database file was written to

### CLI

- `finn-tracker` — launches server at `http://localhost:5050` and opens browser
- `finn-tracker --demo` — loads synthetic sample data for exploration
- `finn-tracker --version` / `finn-tracker --help`
- Data directory at `~/Documents/finn-tracker/` (override with `EXPENSE_TRACKER_DATA`)
- `EXPENSE_TRACKER_DATA` path is resolved and normalized on startup so relative paths and `~` work correctly
- Port override with `EXPENSE_TRACKER_PORT`
- `LLAMA_CPP_TIMEOUT` env var to configure the local LLM request timeout (default 120s)

### AI Chat Assistant

- Built-in chat that answers spending questions in plain English
- Runs locally via llama-server — no external API calls
- Frontend-first architecture: aggregates computed client-side, sent to `/chat` endpoint
- `/chat/config` exposes `dbPath` so the UI always shows which database is active
- Health check at `/chat/status`

### MCP Server

- Model Context Protocol server for Claude Desktop, Cursor, Kiro, and other AI tools
- Shared data access layer (`utils/db.py`) used by both the Flask app and MCP server

### Performance

- Per-file mtime cache (`_scan_cache` in `utils/db.py`) — unchanged files are skipped on subsequent requests within the same server process

### Developer Experience

- Installable via `pip install finn-tracker` (Python 3.9+)
- 472 tests covering parsers, routes, persistence, privacy masking, AI chat, CLI, data access layer, and PDF parsing (including per-bank integration tests for Capital One, Chase, and BofA)
- CI matrix: Python 3.9 / 3.11 / 3.12 on Ubuntu + macOS; PyPI publish on tag
- MIT license
- CONTRIBUTING.md, SCALING.md, GitHub issue templates
