# Changelog

One entry per version. Phases of the MVP are listed under "Unreleased" until v1.0 ships.

## Unreleased (towards v1.0)

### Phase 0.5 — Source validation and recording (2026-11-11)

- Verified with real calls: Yahoo Finance (daily, strip symbols, intraday), Yahoo RSS, SearXNG, CFTC COT. Every v1.0 source now has a status and a verified date; Stooq stays pending a key.
- Source-adapter interface in `backend/app/ingest/` with adapters for Yahoo Finance, Yahoo RSS, Finnhub news, SearXNG and CFTC COT.
- Source-health writer: every job records each source's last success and last error, with keys removed from error text.
- Recorder jobs `record_news`, `record_bars`, `record_cot` and `check_sources` behind `/jobs/...`; three n8n workflow exports in `n8n/`.
- Parquet recording area `data/live/` and snapshot tooling: freeze, manifest, SHA-256 hashes, `datasets` row, verify, DuckDB reader.
- Migration `0002`: `news_items.source_tickers`.
- First backfill: 206,147 daily bars for 27 symbols, about 197,000 intraday bars for the 16 watchlist symbols, 1,941 weekly gold COT reports.
- Massive (ex-Polygon) added on its free plans: futures backup for the strip (most active contract, verified for all 7 futures) and a third news feed. Migration `0003`: `news_items.source_meta` keeps a provider's own sentiment label as a reference only.
- Twelve Data re-probed: it carries no futures.
- Tests: 82 (source contract tests, recorder, health writer, failure injection, snapshots).
- Fix: `.gitignore` ignored the code package `backend/app/data/`; the rule is now anchored to the repo root.

### Phase 0 — Foundations (2026-11-11)

- Repository scaffold: backend, frontend, db, n8n, strategies, sample-data, docs.
- Docker Compose stack: PostgreSQL 16 (no published port), FastAPI, SearXNG, n8n, and a backup container.
- Alembic migration `0001` with the 16 v1.0 tables; fixed menus enforced by CHECK constraints.
- Config seeds: 7 driver tags, 28 tickers with canonical IDs, 12 sources, 19 sensitivity hypotheses, 16 watchlist symbols.
- API: `/health`, `/health/deps`, `/api/sources`, and `/jobs/{name}` protected by a shared secret.
- Frontend: a status page showing service health and the source registry.
- CI: ruff, migrations on a PostgreSQL service container, pytest, frontend lint and build.
- Docs: data sources with verification results, backtesting method with promotion thresholds, monitoring, privacy.
