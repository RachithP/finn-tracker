# Scaling & Performance Improvements

This document outlines performance optimizations and scaling improvements to implement when finn-tracker grows beyond ~10K transactions or experiences slow query times.

---

## Current Architecture

- **Database**: SQLite with WAL mode enabled
- **Query pattern**: Full table scans on every request (no indexes)
- **Data loading**: Auto-scan folders re-parse CSVs on every `/transactions` request
- **AI Chat**: Frontend computes aggregates and sends to backend (no DB query in /chat handler)
- **Caching**: None — all aggregations computed on-demand from raw transaction list

**Works well for**: <10K transactions, local-only usage, single user

**Bottlenecks emerge at**: >50K transactions, slow disk I/O, concurrent MCP + web dashboard usage

---

## AI Chat Architecture (Implemented)

The AI chat uses a **frontend-first** approach to eliminate duplicate data fetching:

1. **Shared config**: `/chat/config` endpoint serves configuration (trend months, transaction limits, periods)
2. **Frontend aggregation**: Dashboard computes summaries using existing functions
3. **Context passing**: Frontend sends pre-computed data with each chat message
4. **Stateless backend**: `/chat` handler formats prompts from frontend data, no DB access

**Benefits:**
- Single source of truth (what user sees = what LLM sees)
- No duplicate DB queries (frontend already loaded transactions)
- Easy to tune context window (change `/chat/config` values)
- Backend stays simple and testable

**To scale later**: Change `trendMonths`, `recentTxnLimit`, `topMerchantsPeriod`, and `topMerchantsLimit` in the `/chat/config` endpoint. The frontend automatically adapts.

---

## Priority 1: Database Indexes

**Problem**: All queries perform full table scans. Filtering by date, category, or merchant requires iterating through every transaction.

**Solution**: Add indexes to `user_transactions` table.

### Implementation

Add to `_init_db()` in `app.py` (after the `CREATE TABLE user_transactions` statement):

```sql
CREATE INDEX IF NOT EXISTS idx_txn_date ON user_transactions(
    json_extract(txn_json, '$.date')
);

CREATE INDEX IF NOT EXISTS idx_txn_category ON user_transactions(
    json_extract(txn_json, '$.category')
);

CREATE INDEX IF NOT EXISTS idx_txn_merchant ON user_transactions(
    json_extract(txn_json, '$.merchant')
);

CREATE INDEX IF NOT EXISTS idx_txn_amount ON user_transactions(
    json_extract(txn_json, '$.amount')
);
```

**Impact**: 10-100x speedup for filtered queries (period filters, category drill-down, merchant search).

**Tradeoff**: Slightly slower writes (negligible for import operations), ~10-20% larger DB file size.

---

## Priority 2: Cache Auto-Scan Results

**Problem**: Every `/transactions` request re-parses all CSVs in `~/Documents/finn-tracker/expense/` and `income/`, even if files haven't changed.

**Solution**: Track file modification times and skip parsing if unchanged.

### Implementation

Add a new table to track parsed files:

```sql
CREATE TABLE IF NOT EXISTS parsed_files (
    file_path TEXT PRIMARY KEY,
    mtime     REAL NOT NULL,
    parsed_at TEXT NOT NULL
);
```

Modify `_load_auto_scan_transactions()` in `app.py`:

```python
def _should_reparse(file_path: Path) -> bool:
    """Check if file needs reparsing based on mtime."""
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT mtime FROM parsed_files WHERE file_path = ?",
            (str(file_path),)
        ).fetchone()
        if not row:
            return True
        return file_path.stat().st_mtime > row["mtime"]

def _mark_parsed(file_path: Path) -> None:
    """Record that a file has been parsed."""
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO parsed_files (file_path, mtime, parsed_at) VALUES (?, ?, ?)",
            (str(file_path), file_path.stat().st_mtime, datetime.now().isoformat())
        )
```

Then wrap the parsing loop:

```python
for folder in auto_scan_folders:
    for file_path in folder.glob("*"):
        if _should_reparse(file_path):
            result = ingest_file(file_path, ...)
            # ... existing logic ...
            _mark_parsed(file_path)
```

**Impact**: 50-90% reduction in `/transactions` response time after first load.

**Tradeoff**: Manual file edits won't be detected unless mtime changes (rare edge case).

---

## Priority 3: Aggregate Caching

**Problem**: Dashboard summary cards, category totals, and monthly trends are recomputed from scratch on every page load.

**Solution**: Cache aggregates in a separate table, invalidate on new transactions.

### Implementation

Add a cache table:

```sql
CREATE TABLE IF NOT EXISTS aggregate_cache (
    cache_key TEXT PRIMARY KEY,
    value     TEXT NOT NULL,
    expires   TEXT NOT NULL
);
```

Wrap expensive aggregations:

```python
def _get_cached_aggregate(key: str, compute_fn, ttl_seconds=300):
    """Return cached value or compute and cache it."""
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT value, expires FROM aggregate_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        if row and datetime.fromisoformat(row["expires"]) > datetime.now():
            return json.loads(row["value"])
        
        result = compute_fn()
        expires = (datetime.now() + timedelta(seconds=ttl_seconds)).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO aggregate_cache (cache_key, value, expires) VALUES (?, ?, ?)",
            (key, json.dumps(result), expires)
        )
        return result
```

Use for:
- Monthly trend (cache key: `trend_{period}`)
- Category totals (cache key: `categories_{period}`)
- Top merchants (cache key: `top_merchants_{period}`)

**Impact**: 2-5x speedup for dashboard loads with no data changes.

**Tradeoff**: Stale data for up to 5 minutes (configurable TTL).

---

## Priority 4: Tune AI Chat Context Window

**Current design**: The AI chat assistant uses a fixed context window to reduce latency:
- `TREND_MONTHS = 12` — monthly summary shows last 12 months
- `RECENT_TXN_LIMIT = 50` — only 50 recent transactions sent to LLM
- Top merchants uses `"1m"` (last 30 days) by default

**Why fixed periods**: The LLM interprets time periods from user queries ("last month", "this year"), so we don't parse them in Python. This keeps the backend simple and offloads natural language understanding to the model.

**Why 1 year default**: Sending all transactions to the LLM on every chat message would cause high latency. 1 year of data covers most queries while keeping context size manageable.

### When to adjust

**Increase limits** if:
- Users frequently ask about data older than 12 months
- LLM responses say "I don't have data for that period"
- You have a faster LLM or more RAM

**Decrease limits** if:
- Chat responses are slow (>5 seconds)
- You have >50K transactions
- Running on low-memory hardware

### Implementation

Edit the constants at the top of the `/chat` handler in `app.py`:

```python
# ── Configurable context window ───────────────────────────────────────────
TREND_MONTHS = 12       # months of history in the monthly summary
RECENT_TXN_LIMIT = 50   # individual transactions shown to the LLM
```

Change to your preferred values:

```python
TREND_MONTHS = 24       # 2 years of monthly trend
RECENT_TXN_LIMIT = 100  # 100 recent transactions
```

The `get_monthly_trend(txns, months)` function already accepts a `months` parameter, so no code changes needed beyond updating the constant.

**Impact**: Larger context = more accurate answers for historical queries, but slower responses.

**Tradeoff**: Linear increase in LLM latency with context size.

---

## Priority 5: Lazy-Load Transactions in Frontend

**Problem**: Dashboard loads all transactions into memory on page load, even if user only views summary cards.

**Solution**: Paginate `/transactions` endpoint and load on-demand.

### Implementation

Add pagination params to `/transactions`:

```python
@app.route("/transactions", methods=["GET"])
def get_transactions():
    offset = int(request.args.get("offset", 0))
    limit = int(request.args.get("limit", 1000))
    
    # ... existing logic ...
    
    return jsonify({
        "transactions": all_txns[offset:offset+limit],
        "total": len(all_txns),
        "offset": offset,
        "limit": limit
    })
```

Update frontend to fetch in batches:

```javascript
async function loadTransactions(offset = 0, limit = 1000) {
    const resp = await fetch(`/transactions?offset=${offset}&limit=${limit}`);
    const data = await resp.json();
    // Append to existing transactions
    return data;
}
```

**Impact**: 10x faster initial page load for users with >10K transactions.

**Tradeoff**: Requires frontend refactor to handle paginated data.

---

## Priority 6: Move to PostgreSQL (Optional)

**When**: >100K transactions, multiple concurrent users, or need for full-text search.

**Why**: SQLite's JSON functions are slower than Postgres's JSONB, and WAL mode has concurrency limits.

**Migration path**:
1. Add `psycopg2` dependency
2. Create a `db_adapter.py` abstraction layer
3. Implement Postgres-specific queries with JSONB operators
4. Provide a migration script: `finn-tracker migrate-to-postgres`

**Impact**: 5-10x speedup for complex queries, better concurrency.

**Tradeoff**: Requires Postgres installation, loses "zero-config" simplicity.

---

## Testing Performance Improvements

Use the `--demo` mode to generate large datasets:

```bash
# Generate 50K transactions
finn-tracker --demo --transactions 50000
```

Benchmark query times:

```python
import time
start = time.time()
# ... run query ...
print(f"Query took {time.time() - start:.2f}s")
```

Target benchmarks:
- `/transactions` (full load): <500ms for 10K txns, <2s for 50K txns
- Category aggregation: <100ms for 10K txns
- Monthly trend: <200ms for 10K txns

---

## Summary

| Priority | Improvement | Effort | Impact | When to implement |
|---|---|---|---|---|
| 1 | Database indexes | 30 min | High | >10K transactions |
| 2 | Cache auto-scan | 2 hours | High | >5K transactions or slow disk |
| 3 | Aggregate caching | 3 hours | Medium | >20K transactions |
| 4 | Tune AI context window | 5 min | Medium | When chat is slow or missing data |
| 5 | Lazy-load frontend | 4 hours | High | >10K transactions |
| 6 | Postgres migration | 8 hours | Very High | >100K transactions |

**Start with Priority 4** (tune context) — it's a 1-line config change to balance latency vs. data coverage.

**Implement 1-3 together** when you hit performance issues — they're complementary and share DB schema changes.
