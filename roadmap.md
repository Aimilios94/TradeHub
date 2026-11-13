# Roadmap

Six MVP phases over about 16 part-time weeks, then five later versions. A phase starts only when the previous one passes its acceptance list, so dates move but the order does not. Full detail: [docs/PLAN.pdf](docs/PLAN.pdf).

## MVP (v1.0)

### Phase 0 — Foundations (weeks 1–2)

- [x] Repo scaffold; README, DECISIONS, APP_OVERVIEW, CHANGELOG
- [x] Docker Compose: PostgreSQL 16, FastAPI, SearXNG, n8n; Ollama on the host
- [x] PostgreSQL with a persistent volume and no published port; Alembic migrations for the v1.0 tables; seeds
- [x] `pg_dump` backup from day 1
- [x] Secrets in env files, never in git
- [x] CI: ruff, migrations, pytest, frontend build
- [x] Promotion thresholds in `docs/BACKTESTING.md`
- [ ] Optional: apply for Reddit API access

**Accept:** schema migrates clean; CI green; seeds loaded; compose boots end-to-end.

### Phase 0.5 — Source validation + start recording (week 3)

- [x] Every v1.0 source: one successful call, logged in `docs/DATA_SOURCES.md` (12 working; Stooq dropped, key pending; Massive is the futures backup)
- [x] Record the fallback decision for each failure
- [x] Daily recorder built: news headlines, daily and intraday bars for the watchlist
- [x] Recorder schedules published in n8n and seen running on their own (first scheduled run 2026-11-11 22:30 UTC)
- [x] First daily backfill, frozen as snapshot v1

**Accept:** every source marked working, fallback or dropped; recorder running daily; snapshot v1 in `datasets`.

### Phase 1 — Equities pipeline (weeks 4–6)

- [x] Ingest adapters: Yahoo RSS, Finnhub news, StockTwits, SearXNG
- [x] Dedupe, importance score, driver tagger v1
- [x] Hermes condenser with Pydantic validation, one retry, `llm_traces` (provisional model, D57, until the Phase 4 model eval)
- [x] Composite sentiment score per ticker per day
- [ ] Start labeling 100 news items (set drawn and labeling tool built 2026-11-12; the labels are the owner's to make)
- [x] Golden day committed (synthetic headlines; real news text is never committed)

**Accept:** 3 consecutive days of hands-off ingest; zero fabricated rows; dedupe rate sane; 100% of Hermes rows valid or logged as failures; golden-day test green.

Acceptance state on 2026-11-13: golden-day test green; the `process news` workflow was published on 2026-11-12. Hands-off days so far: 0 of 3. 2026-11-12 does not count (a failed news run and manual runs), and 2026-11-13 does not count (the 13:00 UTC news run and the 13:15 UTC pipeline run failed with connection errors). The earliest possible day 1 is 2026-11-14.

### Phase 2 — Context layer + UI (weeks 7–9)

- [ ] Quotes and futures strip with delayed labels; sector heatmap
- [ ] Fed and macro panel
- [ ] Earnings dates on Ticker Pages and watchlist
- [ ] Dashboard, Market Pulse Equities tab, Ticker Pages, Settings
- [ ] Importance narrator and Fed explainer behind the number check

**Accept:** TSLA, SPY and GLD pages complete; all delayed data labeled; next 3 macro events visible with countdown; no Hermes text shown that fails the number check.

### Phase 3 — Historical data + backtester (weeks 10–13)

- [ ] Full backfill into a frozen snapshot
- [ ] Strategy-config engine and building blocks v1
- [ ] Backtester with the fill model and metrics
- [ ] Gap fade first, cross-checked in backtesting.py
- [ ] Price action supply/demand, VIX spike, FOMC drift, Gold + Dollar + COT; SLC as the alternative
- [ ] Strategy Lab UI

**Accept:** reruns byte-identical in CI; gap fade cross-check within tolerance; look-ahead test green; one RESULTS.md per strategy run, failures included.

### Phase 4 — Evals, hardening, MVP gate (weeks 14–16)

- [ ] Condenser, model-comparison, tagger and number evals; baselines committed; cheap tier decided
- [ ] Regression gate in CI with a mock
- [ ] Retry and rate-limit guards, failure-injection tests, tested restore
- [ ] README Results section filled

**MVP gate:** 5 hands-off weekdays; Dashboard shows market state at a glance; at least 4 strategies backtested reproducibly, one cross-checked; eval baselines committed; zero invented numbers shown; LLM cost under €5 for the month; every failure shows a real error.

## After the MVP

| Version | Weeks | Scope |
|---|---|---|
| v1.0.5 | 17–20 | Paper sim, journal, morning briefing, Ask-Hermes, ticker summary |
| v1.0.8 | 21–24 | Deep Analysis, strategy suggester and hypothesis loop, regime filter |
| v1.1 | 25–27 | Crypto |
| v1.2 | 28–30 | Commodities |
| v1.3 | 31+ | Smart Money |
