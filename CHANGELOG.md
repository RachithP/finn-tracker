# Changelog

All notable changes to finn-tracker are documented here.

## [0.1.0] - 2026-04-17

Initial public release.

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
- PDF table extraction with text-fallback (pdfplumber)
- Improved error messages for parse failures

### Categorization

- Static rules auto-categorize common merchants
- Manual overrides persisted and learned as reusable merchant rules
- Learned rules applied server-side on every transaction fetch

### CLI

- `finn-tracker` — launches server at `http://localhost:5050` and opens browser
- `finn-tracker --demo` — loads synthetic sample data for exploration
- `finn-tracker --version` / `finn-tracker --help`
- Data directory at `~/Documents/finn-tracker/` (override with `EXPENSE_TRACKER_DATA`)
- Port override with `EXPENSE_TRACKER_PORT`

### AI Chat Assistant

- Built-in chat that answers spending questions in plain English
- Runs locally via llama-server — no external API calls
- Frontend-first architecture: aggregates computed client-side, sent to `/chat` endpoint
- Health check at `/chat/status`

### MCP Server

- Model Context Protocol server for Claude Desktop, Cursor, Kiro, and other AI tools
- Shared data access layer (`utils/db.py`) used by both the Flask app and MCP server

### Developer Experience

- Installable via `pip install finn-tracker` (Python 3.9+)
- 324 tests covering parsers, routes, persistence, privacy masking, AI chat, CLI, data access layer, and PDF parsing
- CI matrix: Python 3.9 / 3.11 / 3.12 on Ubuntu + macOS; PyPI publish on tag
- MIT license
- CONTRIBUTING.md, SCALING.md, GitHub issue templates
