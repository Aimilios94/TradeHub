# Changelog

One entry per version. Phases of the MVP are listed under "Unreleased" until v1.0 ships.

## Unreleased (towards v1.0)

### Phase 1 — Equities pipeline (built 2026-11-12; acceptance still open)

- Dedupe across feeds: same or contained title within 72 hours writes `duplicate_of`. 17.7% of the first 1,563 recorded headlines were duplicates.
- Driver tagger v1: keyword rules for the 7 tags in `backend/app/tagger/rules.json`, with direction and the scheduled flag, written to `tagged_events`.
- Importance score v1 (0–100) with its components stored per headline; ranking of a day.
- Hermes condenser (template 1): versioned prompt file, Pydantic schema, menu checks, number check, one retry with the reason, every call in `llm_traces`, daily and per-run caps, monthly budget stop. Ran on local Ollama `llama3.2` at first; since 2026-11-12 on `mistralai/mistral-small-3.2-24b-instruct` through OpenRouter (D57), provisional until the Phase 4 model eval. Caps raised to 1,000 calls a day and 400 a run (D58).
- Number check (`backend/app/hermes/number_check.py`) with the plan's rounding rule.
- StockTwits adapter and one sample per ticker per day (counts only).
- Composite sentiment score per ticker per day in `daily_scores`. Now `composite-v2` (D56): the driver input is scaled by the number of driver tags, so one tag no longer gives a ticker without headlines the full -100 or +100.
- Job `process_news` and n8n workflow `process_news.json` (07:15, 13:15, 21:45 UTC).
- Read-only API: `/api/news/top` and `/api/scores`.
- Golden day: 24 synthetic headlines and `expected.json` in `sample-data/golden_day/`, with a test.
- Labeling tool `scripts/label_set.py`: draws the 100-item set, labels in the terminal, exports IDs and labels only.
- Migration `0004`: `news_items.processed_at` and `importance_components`, `llm_traces.news_item_id`, tagger columns on `tagged_events`, table `social_sentiment`.
- Tests: 206.
- Not done yet: the 100 labels (owner), and the acceptance run of 3 hands-off days.

### Phase 0.5 — Source validation and recording (2026-11-11)

- Verified with real calls: Yahoo Finance (daily, strip symbols, intraday), Yahoo RSS, SearXNG, CFTC COT. Every v1.0 source is now working or dropped: 12 working, and Stooq dropped (key pending). The source seed holds 13 rows.
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
