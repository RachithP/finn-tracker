# Contributing to finn-tracker

Thanks for your interest! finn-tracker is a privacy-first local expense tracker.
All contributions must preserve the core privacy guarantee: **no data ever leaves the user's machine**.

---

## Getting started

Dev setup uses [uv](https://docs.astral.sh/uv/) (see the [uv installation docs](https://docs.astral.sh/uv/getting-started/installation/)):

```bash
git clone https://github.com/RachithP/finn-tracker.git
cd finn-tracker
uv sync --extra dev --locked --python 3.12
```

`uv sync` reads `uv.lock` and builds a `.venv` with the exact resolved dependency graph — no separate `uv venv`/`pip install` step needed, and no manual `requirements.txt` to keep in sync. `--locked` asserts `uv.lock` is already up to date with `pyproject.toml` and fails instead of silently re-resolving it — if you've added or changed a dependency, run `uv lock` yourself and commit the updated `uv.lock`.

To run the app in dev mode with sample data — `uv run` runs a command inside that `.venv` without activating it:

```bash
# Generate synthetic sample data into sample_data/ (no real bank data needed)
uv run python -c "from sample_data.generators import write_demo_files; write_demo_files('./sample_data/expense')"

# Start the app pointing at that folder
EXPENSE_TRACKER_DATA=./sample_data uv run finn-tracker
```

(Prefer an activated shell instead? `source .venv/bin/activate`, then drop the `uv run` prefix from the commands above.)

This keeps real bank files out of the repo entirely. Generated CSV files in `sample_data/` are gitignored.

---

## Running tests

> Before running tests, ask the user which Python environment to use. Do not probe the filesystem to discover it. Example: "Which Python environment should I use? (e.g. the repo's uv-managed `.venv`, or another interpreter path)"

```bash
# Replace <python> with the binary the user specifies (typically .venv/bin/python)
<python> -m pytest tests/ -v
```

All 495 tests live in `tests/`:
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

`sample_data/generators.py` contains synthetic CSV and PDF fixtures (100% fake data, no PII) used by the test suite.

**To add a new CSV format:**

1. Add a `YOUR_BANK_CSV` string constant to `sample_data/generators.py`
2. Add it to the `write_sample_files()` function
3. Add detection logic in `parsers/csv_parser.py` (see `_detect_format()`)
4. Add tests in `tests/test_app.py` referencing the new constant

**To add a new PDF format:**

1. Add a `make_yourbank_pdf(output_dir)` generator to `sample_data/generators.py` using reportlab — use the existing Capital One / Chase / BofA generators as a reference
2. Add it to `write_sample_pdf_files()` and add a `YOURBANK_PDF_TRANSACTION_COUNT` constant
3. If the bank uses short-form dates or inverted charge signs, verify `invert_charges` and `year_hint` logic in `parsers/pdf_parser.py` handles it correctly
4. Add per-bank test classes in `tests/test_pdf_parser.py`: unit tests for text-line parsing, mock-based pipeline tests, and an end-to-end integration test class

> **Important:** Always use clearly fake account numbers (e.g. `1234`, `5678`) in generators and tests. Never copy last-4 digits or any other identifier from a real bank file — not in code, not in commit messages, not in PR descriptions. See Guideline #6 in CLAUDE.md.

> **Note:** `finn_tracker` is the Python module name (import path); `finn-tracker` is the PyPI distribution name. These are intentionally different.

---

## PR checklist

- [ ] Tests pass: `<python> -m pytest tests/ -v`
- [ ] If you added/changed a dependency in `pyproject.toml`, ran `uv lock` and committed the updated `uv.lock` (`uv sync --extra dev --locked` fails if you forget)
- [ ] No outbound network calls introduced
- [ ] New merchant/account data goes through `mask_sensitive()` before any API response
- [ ] CHANGELOG.md updated under `## [Unreleased]`
- [ ] No absolute system paths added to docs or code (use `~/`, relative paths, or `<placeholder>` instead)
- [ ] No real account numbers, card last-4 digits, or financial totals from actual bank files appear in code, tests, commit messages, or PR descriptions (see Guideline #6 in CLAUDE.md)
