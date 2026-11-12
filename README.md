# TradeHub

A personal web app that collects free market news and data, ranks what matters each day, has an LLM ("Hermes") explain it in plain words, and tests the owner's strategies on past data. Every trade is made by hand elsewhere: there is no broker connection and no live money in this app.

**Status:** Phases 0 and 0.5 are complete. Phase 1 (the equities news pipeline) is built and waiting on its acceptance run: recorded headlines are deduplicated, tagged, ranked, condensed by a cheap model on OpenRouter and turned into a daily score per ticker. None of it has been evaluated yet and there is no UI for it yet. The full plan is in [docs/PLAN.pdf](docs/PLAN.pdf).

## Principles

1. **Code computes, Hermes concludes.** Scores, rankings, indicators and backtests are deterministic code. Hermes only condenses and narrates, and an automatic number check blocks any text containing a figure it was not given.
2. **Strategies are data.** Every strategy is a JSON config; changing a parameter needs no code.
3. **Tags from a fixed menu.** Driver tags, catalyst types and directions come from fixed lists, enforced in the database.
4. **Fail honest.** A source that is down shows a real error, an alert and a stale label. Never fake data.
5. **Measure before trusting.** Hermes outputs, the tagger and every signal are evaluated against labeled data first.
6. **Small vertical slices.** A phase starts only when the previous one passes its acceptance list.
7. **€0–5 per month.**

## Features by version

| Version | Scope | State |
|---|---|---|
| v1.0 (MVP) | Equities news pipeline, Dashboard, Market Pulse, Ticker Pages, daily-bar backtester with 6 starter strategies, Hermes evals | Phases 0 and 0.5 done; Phase 1 built, acceptance open |
| v1.0.5 | Paper sim, journal, morning briefing, Ask-Hermes | Planned |
| v1.0.8 | Deep Analysis, strategy suggestions, regime filter | Planned |
| v1.1 | Crypto | Planned |
| v1.2 | Commodities | Planned |
| v1.3 | Smart Money (insider filings) | Planned |

See [roadmap.md](roadmap.md) for phases and acceptance lists, and [APP_OVERVIEW.md](APP_OVERVIEW.md) for what each section does.

## Architecture

```
sources ──> Python jobs ──> PostgreSQL (app data) ──────────────> FastAPI ──> React UI
   │            │                                                    ▲         (read-only)
   │            └──> Hermes (condense, narrate) ──> number check ────┘
   └──> backfill ──> frozen Parquet snapshots (DuckDB) ──> backtester ──> PostgreSQL
```

- **n8n** only triggers jobs on a schedule. All logic is Python.
- **PostgreSQL 16** runs in Docker, publishes no port, and holds app data only.
- **Price and macro history** lives in frozen, versioned Parquet snapshots read through DuckDB.
- Everything is stored in UTC.

## Results

Nothing has been measured yet. This section will hold condenser accuracy, tagger precision and recall, the invented-number rate, the model comparison and backtest results with their caveats. No number appears here until an eval or a backtest has produced it.

## How to run

Requirements: Docker, and Ollama running on the host. For development: [uv](https://docs.astral.sh/uv/) and Node 24.

```sh
cp .env.example .env        # then fill in the keys and generate the three secrets
docker compose up -d --build
```

| Service | Address | Notes |
|---|---|---|
| API | http://127.0.0.1:8100/health | `/health/deps` shows database, SearXNG and Ollama |
| n8n | http://127.0.0.1:5680 | Triggers only |
| SearXNG | http://127.0.0.1:8888 | JSON output enabled |
| PostgreSQL | not published | Reachable only inside the Compose network |

The API container applies migrations and loads the config seeds on every start. A `backup` container writes a `pg_dump` to `backups/` at start and every 24 hours.

Frontend (development):

```sh
cd frontend && npm install && npm run dev    # http://localhost:5273
```

Recording and snapshots:

```sh
docker compose exec n8n n8n import:workflow --separate --input=/workflows   # then publish each in the n8n editor
docker compose exec api python -m scripts.backfill          # full daily history, recent intraday bars, COT
docker compose exec api python -m scripts.snapshot freeze   # frozen copy in data/v<date>/, registered in `datasets`
docker compose exec api python -m scripts.snapshot verify v2026-11-11
```

News pipeline and labels:

```sh
docker compose exec n8n n8n import:workflow --input=/workflows/process_news.json   # then publish it
curl "http://127.0.0.1:8100/api/news/top?limit=10"          # today's ranked headlines (UTC day)
curl "http://127.0.0.1:8100/api/scores"                     # today's composite score per ticker
docker compose exec api python -m scripts.label_set label   # label the 100-item eval set by hand
```

Tests and lint:

```sh
docker compose exec api pytest               # needs the running stack (uses a throwaway database)
cd backend && uv run ruff check . ../db
```

## Repository layout

```
backend/app/      ingest, scoring, tagger, hermes, market, backtest, data, api
backend/tests/    pytest suite
backend/scripts/  CLI entry points (seeds, backfill, snapshot, golden day, labeling; evals later)
backend/eval/     hand labels (IDs only), eval results and baselines
frontend/         React + Vite
strategies/       one JSON config per strategy (Phase 3)
db/               Alembic migrations, config seeds, backup script
n8n/              workflow exports (triggers only)
data/             live recordings and frozen Parquet snapshots (git-ignored)
sample-data/      golden day (synthetic headlines) and small samples for CI
docs/             plan, data sources, backtesting method, monitoring, privacy
```

Decisions and their reasons are in [DECISIONS.md](DECISIONS.md); changes per version are in [CHANGELOG.md](CHANGELOG.md).
