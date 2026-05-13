# Scaling & Performance

Reference for when finn-tracker grows beyond ~10K transactions or feels slow.

**Works well for**: <10K transactions, local-only, single user  
**Bottlenecks emerge at**: >50K transactions, slow disk I/O, concurrent MCP + web dashboard usage

---

## Priority 1: Database Indexes

**Problem**: `user_transactions` stores rows as JSON blobs with no indexes. All filtering (by date, category, merchant) scans every row.

**Solution**: Add expression indexes on `json_extract(txn_json, '$.date')`, `$.category`, `$.merchant`, `$.amount` in `_init_db()`.

**Impact**: 10–100x speedup for filtered queries.  
**Tradeoff**: ~10–20% larger DB file, slightly slower writes (negligible for import-sized batches).

---

## Priority 2: Persist mtime Cache Across Restarts ✅ (in-memory implemented)

**Problem**: The current in-memory mtime cache (`_scan_cache` in `utils/db.py`) is lost on server restart, so the first request after a restart re-parses all files.

**Solution**: Persist parsed-file mtimes to a `parsed_files` SQLite table; check it before parsing on startup.

**Impact**: 50–90% faster first request after restart.  
**Tradeoff**: Stale cache if files are edited externally without touching mtime (rare).

---

## Priority 3: Aggregate Caching

**Problem**: Summary cards, category totals, and monthly trends are recomputed from every transaction on every page load.

**Solution**: Cache aggregates in a `aggregate_cache` SQLite table with a TTL (e.g. 5 minutes); invalidate on new imports.

**Impact**: 2–5x faster dashboard loads with no data changes.  
**Tradeoff**: Up to 5-minute stale window (configurable).

---

## Priority 4: Tune AI Chat Context Window

**Problem**: Sending too many months of trend history to the LLM increases latency linearly.

**Solution**: Change `maxTrendMonths` in the `/chat/config` endpoint (`app.py`). The frontend caps its monthly-trend history to that value automatically.

**Impact**: Halving the context roughly halves chat latency.  
**Tradeoff**: Less historical data available for LLM answers.

---

## Priority 5: Lazy-Load Transactions in Frontend

**Problem**: All transactions load into memory on page load, even if the user only views summary cards.

**Solution**: Paginate `GET /transactions` with `offset` + `limit` params; load additional pages on demand in the frontend.

**Impact**: 10x faster initial page load for >10K transactions.  
**Tradeoff**: Requires frontend refactor to handle paginated state.

---

## Priority 6: PostgreSQL (Optional)

**When**: >100K transactions, multiple concurrent users, or need for full-text search.

**Why**: SQLite WAL mode has concurrency limits; Postgres JSONB operators are faster for complex queries.

**Migration path**: add `psycopg2`, create a `db_adapter.py` abstraction, implement Postgres-specific queries, provide a `finn-tracker migrate-to-postgres` command.

**Impact**: 5–10x speedup for complex queries, better concurrency.  
**Tradeoff**: Requires Postgres installation, loses zero-config simplicity.

---

## Summary

| Priority | Improvement | Effort | Impact | When |
|---|---|---|---|---|
| 1 | DB indexes | 30 min | High | >10K transactions |
| 2 | Persist mtime cache | 1 hour | Medium | Slow restarts |
| 3 | Aggregate caching | 3 hours | Medium | >20K transactions |
| 4 | Tune AI context window | 5 min | Medium | Chat is slow |
| 5 | Lazy-load frontend | 4 hours | High | >10K transactions |
| 6 | PostgreSQL | 8 hours | Very High | >100K transactions |
