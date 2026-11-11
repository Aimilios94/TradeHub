# Decisions

Every locked or rejected decision with its reason. Settled items are not re-opened without new evidence.

## Locked (from the v2.1 plan, 2026-11-11)

| # | Decision | Reason |
|---|---|---|
| D1 | MVP = Equities only: pipeline, Dashboard, Ticker Pages, daily backtester, Hermes evals | One finished category beats three half-built |
| D2 | Stack: React + Vite, FastAPI, PostgreSQL 16 in Docker, DuckDB + Parquet, n8n, Docker Compose | Self-hosted, €0 |
| D3 | n8n only triggers jobs; all logic in Python | Logic stays testable and versioned |
| D4 | Everything stored in UTC; sessions from a calendar library | No timezone bugs in backtests |
| D5 | Prices and macro history in frozen Parquet snapshots, never in PostgreSQL | Reproducible backtests; small database |
| D6 | MVP backtests use ETFs only (SPY, QQQ, GLD). Yahoo `=F` series are display-only | No contract rolls needed |
| D7 | No USO as an oil proxy; oil backtests wait for back-adjusted CL in v1.2 | Contango decay; 2020 structure change |
| D8 | Driver taxonomy locked at 7 tags plus a scheduled flag | Classification accuracy |
| D9 | Hermes never computes numbers; a number check blocks any output with a figure not in its input | Core principle 1 |
| D10 | No LLM-as-judge in evals | Labels are hand-made |
| D11 | Pass/fail on expectancy and profit factor after costs; win rate never decides | Avoids overfitting to win rate |

## Decided during Phase 0 (2026-11-11)

| # | Decision | Reason |
|---|---|---|
| D12 | PostgreSQL in Docker replaces Supabase | Supabase free plan allows 2 projects, both in use |
| D13 | Better tier model: `nousresearch/hermes-4-405b` on OpenRouter | The only Hermes model OpenRouter lists; volume is a few calls a day, so cost stays inside budget |
| D14 | Promotion thresholds as written in [docs/BACKTESTING.md](docs/BACKTESTING.md) | Owner's starting values; to be tightened after the first runs |
| D15 | Seed watchlist: SPY, QQQ, GLD, TSLA, NVDA and the 11 sector ETFs; everything else is added in the UI | Owner's choice |
| D16 | Alerts go to Telegram, sent by n8n | Owner's choice; wired when alerts are built |
| D17 | TradeHub runs its own n8n container on host port 5680 | Independent of other projects on the same machine |
| D18 | Strip source order: Yahoo, then Twelve Data, then Stooq | See D19 and D20 |
| D19 | Stooq is "pending": programmatic access needs an API key, requested 2026-11-11 | Scripts get an HTML page instead of CSV |
| D20 | Twelve Data free plan is not treated as a strip backup for indices or futures | Probe: SPX and GC need paid plans; VIX, DXY, NQ rejected |
| D21 | StockTwits is additive only, with a minimum count of labeled messages before it enters the composite score | Each call returns a small sample of recent messages |
| D22 | Fixed menus (catalyst type, directions, statuses) are CHECK constraints in PostgreSQL as well as Pydantic rules | A bad value cannot be stored even if validation is bypassed |
| D23 | Migrations are hand-written SQL run by Alembic; no ORM models | About 20 tables; SQL is the clearest description of them |
| D24 | The API container applies migrations and config seeds on every start | One command boots a correct database |
| D25 | Backups run in a sidecar container (`pg_dump` at start and every 24 hours), not from n8n | A backup must not depend on the scheduler being configured |
| D26 | Python 3.12 in Docker and CI | Data libraries used later lag the newest Python release |
| D27 | Host ports: API 8100, SearXNG 8888, n8n 5680, Vite 5273, all on 127.0.0.1 | 8000 and 5173 are used by other projects on the machine |

## Rejected

| Item | Reason |
|---|---|
| Alpaca or any broker execution | Owner trades by hand; removes the riskiest part |
| Supabase | See D12 |
| Finviz Elite | No free API |
| CryptoPanic | Free developer plan ended April 2026 |
| X/Twitter API | Paid |
| StockTitan | No public developer API |
| Brave Search API | Free tier replaced by metered billing in Feb 2026 |
| Alpha Vantage | 25 requests a day is too few |
| Massive (ex-Polygon), US Treasury API | Overlap with Tiingo and FRED |
| A 4th category "Indices" | Covered by the strip and index tickers |
| Per-category LLM prompts | One engine, one template set, different configs |
| Real-time CME or paid feeds | Delayed and daily data fit this use |
| Taxonomy beyond about 10 tags | Classification accuracy |
| Prices in PostgreSQL | See D5 |
| 8-week MVP | Too much for part-time work |

## Open questions (answered by tests or evals, not opinion)

1. Cheap tier: Ollama or a small OpenRouter model. Decided by `eval_models.py` in Phase 4.
2. Databento intraday backfill: decided at the start of Phase 3 from the cost estimate.
3. Crypto paper trading in v1.1 or later: decided at the v1.1 start.
4. SLC Stochastic settings: defaults (14, 3, 3; 80/20; %K) until the owner's chart settings replace them.
5. Price-action defaults (markets, first-touch zones, stop-first, shorts allowed): confirmed or changed after the first Phase 3 run.
