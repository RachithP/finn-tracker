# finn-tracker Agent Guide

Rules and context for any AI agent working in this repository.
This file is read by OpenAI Codex, GitHub Copilot, Cursor, and similar tools.
Claude Code uses `CLAUDE.md` at the repo root — keep the two files in sync.

---

## What this project is

A fully local expense tracking and visualization tool. Parses bank CSV/PDF statements,
auto-categorizes transactions, and renders an interactive dashboard. **No data ever
leaves the machine.** Privacy is a first-class requirement, not a nice-to-have.

---

## Hard rules — never violate these

- **No external network calls.** The server binds to `127.0.0.1` only. Do not add
  outbound HTTP, telemetry, analytics, or any call that leaves localhost.
- **No new external dependencies** without explicit user approval.
- **Preserve the sign convention.** `amount` is negative for charges/debits, positive
  for credits/payments. This convention flows through parsers → models → API → frontend.
  Flipping it anywhere breaks everything downstream.
- **Do not commit real banking data.** Use synthetic fixtures in `tests/fixtures/` only.
- **All 324 tests must pass** before considering any change complete.
- **No system paths in docs or code.** Never hardcode absolute paths (e.g. `/Users/username/...`) in documentation, comments, or committed code. This is a public repository. Use `~/`, relative paths, or generic placeholders like `<python>` instead.

---

## Architecture at a glance

```
app.py              Flask backend — API routes, SQLite persistence, privacy masking
ingest.py           Routes .csv/.pdf files to the correct parser
models.py           Transaction dataclass, ParseResult, mask_sensitive(), DEFAULT_CATEGORIES, autocat()
mcp_server.py       MCP server for Claude Desktop, Claude Code, Cursor, Kiro
parsers/
  csv_parser.py     Auto-detects Chase Bank, Chase Credit, BofA, Capital One, or generic CSV
  pdf_parser.py     Table + text-fallback extraction (pdfplumber)
utils/db.py         Shared data-access layer (no Flask import) — used by app.py and mcp_server.py
finn_tracker/
  __main__.py       CLI entry point (finn-tracker command)
  dashboard/
    index.html      Entire frontend — vanilla JS, no build step
~/Documents/finn-tracker/
  expense/          CSVs/PDFs auto-loaded on every GET /transactions
  income/           Same, treated as income source
  finn_tracker.db   SQLite DB (gitignored); WAL mode for concurrent reads
```

---

## How to run and test

> Before running any tests or Python commands, ask the user which Python environment to use. Do not probe the filesystem to discover it. Example: "Which Python environment should I use? (e.g. conda env name, venv path)"

```bash
# Start the app
finn-tracker   # opens http://localhost:5050

# Run all tests (replace <python> with the binary the user specifies)
<python> -m pytest tests/test_app.py -v

# Single test class or test
<python> -m pytest tests/test_app.py::TestParsers -v
```

All 324 tests live in `tests/`:
- `test_app.py` — parsers, Flask routes, persistence, AI chat
- `test_cli.py` — CLI, packaging
- `test_db.py` — shared data access layer, analytics, period filtering
- `test_ingest.py` — file routing, multi-file ingestion
- `test_pdf_parser.py` — PDF parsing, account detection, table/text extraction

---

## Key conventions

- `txn_id` is a stable 12-char MD5 hash of `(date|merchant|amount|account)`. Never change
  how it's computed without migrating the DB.
- Category overrides and learned rules are stored in SQLite and survive restarts.
- Folder-scanned transactions (`~/Documents/finn-tracker/expense/`, `income/`) are re-parsed on every
  `GET /transactions` — they are intentionally not cached.
- `mask_sensitive()` in `models.py` masks card numbers, account numbers, and SSNs before
  any JSON leaves the server. It must be called on every response path.
- `utils/db.py` has no Flask import — it is shared by `app.py` and `mcp_server.py`.
  Keep it free of Flask dependencies.

---

## Named agents

### `finn-tracker.parser`
Scope: `parsers/`, `ingest.py`, `models.py`
Focus on parser format support, bank statement detection, edge-case handling.
Do not touch frontend or Flask routes.

### `finn-tracker.backend`
Scope: `app.py`, `utils/`
Focus on Flask route behavior, import/export flows, privacy masking, duplicate detection.
Preserve local-only operation; do not introduce external networking.

### `finn-tracker.ui`
Scope: `finn_tracker/dashboard/index.html`
Focus on dashboard behavior and API integration.
Keep the frontend thin — do not move business logic into the browser.

### `finn-tracker.tests`
Scope: `tests/`
Run and extend all test files in `tests/`. Confirm sign conventions, parser edge cases,
and DB persistence behavior. Do not change app design beyond what tests require.
