# Contributing to finn-tracker

Thanks for your interest! finn-tracker is a privacy-first local expense tracker.
All contributions must preserve the core privacy guarantee: **no data ever leaves the user's machine**.

---

## Getting started

```bash
git clone https://github.com/RachithP/finn-tracker.git
cd finn-tracker
pip install -e ".[dev]"
```

To run the app in dev mode with sample data:

```bash
# Generate synthetic sample data into sample_data/ (no real bank data needed)
python -c "from sample_data.generators import write_demo_files; write_demo_files('./sample_data/expense')"

# Start the app pointing at that folder
EXPENSE_TRACKER_DATA=./sample_data finn-tracker
```

This keeps real bank files out of the repo entirely. Generated CSV files in `sample_data/` are gitignored.

---

## Running tests

> Before running tests, ask the user which Python environment to use. Do not probe the filesystem to discover it. Example: "Which Python environment should I use? (e.g. conda env name, venv path)"

```bash
# Replace <python> with the binary the user specifies
<python> -m pytest tests/ -v
```

All 324 tests live in `tests/`:
- `test_app.py` and `test_cli.py` — parsers, Flask routes, persistence, privacy masking, CLI
- `test_db.py` — shared data access layer, analytics, period filtering
- `test_ingest.py` — file routing, multi-file ingestion
- `test_pdf_parser.py` — PDF parsing, account detection, table/text extraction

---

## Privacy principles

Every contribution must follow these rules:

1. **No outbound network calls** — the app must never send data to any external server.
2. **No PII in API responses** — all merchant names, account numbers, and card numbers must pass through `mask_sensitive()` before being returned by any endpoint.
3. **Server binds to 127.0.0.1 only** — never `0.0.0.0` by default.
4. **Temporary files are deleted immediately** after parsing — never stored beyond the parse step.

---

## Adding a new bank parser

`sample_data/generators.py` contains synthetic CSV fixtures (100% fake data, no PII) used by the test suite.

To add support for a new bank:

1. Add a `YOUR_BANK_CSV` string constant to `sample_data/generators.py`
2. Add it to the `write_sample_files()` function
3. Add detection logic in `parsers/csv_parser.py` (see `_detect_format()`)
4. Add tests in `tests/test_app.py` referencing the new constant

> **Note:** `finn_tracker` is the Python module name (import path); `finn-tracker` is the PyPI distribution name. These are intentionally different.

---

## PR checklist

- [ ] Tests pass: `<python> -m pytest tests/ -v`
- [ ] No outbound network calls introduced
- [ ] New merchant/account data goes through `mask_sensitive()` before any API response
- [ ] CHANGELOG.md updated under `## [Unreleased]`
- [ ] No absolute system paths added to docs or code (use `~/`, relative paths, or `<placeholder>` instead)
