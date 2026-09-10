# TradeHub

Personal, single-user trading hub. **Hermes proposes, the Python engine proves, you decide.**
Plain-language map: [APP_OVERVIEW.md](APP_OVERVIEW.md) · Full spec: [tradehub_implementation_plan.md](tradehub_implementation_plan.md) (v1.3) · UI reference: [dashboard_mockup.html](dashboard_mockup.html) · Decisions taken during the build: [docs/DECISIONS.md](docs/DECISIONS.md)

## Architecture (thin n8n, fat Python)
- **`engine/`** — Python 3.12 FastAPI service. All rules, math, scanning, Guardian gate, execution, backtests, analytics. Every pipeline step is an endpoint with tests.
- **n8n** (Docker) — crons in ET, Telegram, inbound webhooks. Calls engine endpoints; holds no logic.
- **Supabase** — Postgres (`supabase/migrations/`), Edge Function market proxy (Phase 1). All state lives here.
- **Ollama** (native Windows, GPU) — Hermes 3 8B for bulk summarization. **OpenRouter** — Hermes 3 70B/405B for judgment.
- **`prompt-library/`** — every strategy, framework, and prompt; the DB seed is generated from it.

## Repository layout
```
engine/            FastAPI service — app/{registry,models,config,db,auth,notify,main}.py, app/seed/, app/marketdata/ (Phase 1), app/scanner/ + app/detectors/basic.py (Phase 2), app/{exits,guardian,execution,backtest,analytics}/
  scripts/gen_seed.py   generates supabase/migrations/0002_seed.sql (single source of truth = Python + prompt-library)
  tests/                pytest
supabase/migrations/   0001_core_schema.sql · 0002_seed.sql (GENERATED) · 0003_premarket_profile.sql · 0004_symbol_stats.sql
supabase/functions/    market-proxy (Deno Edge Function — the frontend's only market-data door)
prompt-library/        strategies/ frameworks/ hermes-core/ hermes-extensions/ extension-modules/ tests/ _originals/
n8n/workflows/         exported workflow JSON (01 nightly baseline, more per phase)
docker-compose.yml     engine + n8n
.env.example           every key the project will ever need
```

## Setup (this Windows PC)
1. **Tools:** Git, Python 3.12 (installed 2026-09-08), Docker Desktop (installed; must be running), Node 22 LTS (Phase 1 for the Supabase CLI; Phase 9 for the UI), [Ollama for Windows](https://ollama.com) then `ollama pull hermes3:8b`.
2. **Engine dev loop**
   ```powershell
   cd engine
   python -m venv .venv
   .\.venv\Scripts\python -m pip install -e ".[dev]"
   .\.venv\Scripts\python -m pytest -q
   .\.venv\Scripts\python -m uvicorn app.main:app --reload     # http://localhost:8000/health, /plugins, /seed/strategies, /docs
   # /data/* and /scan/* need:  -H "Authorization: Bearer <ENGINE_API_TOKEN>"
   #   e.g. GET /data/premarket/PLUG · POST /scan/run?kind=morning&dry_run=true · GET /scan/latest · GET /scan/universe/PLUG
   ```
3. **Database:** create a Supabase project → apply `supabase/migrations/` in order (0001 schema, 0002 seed, 0003 pre-market profile). From this PC use the session-pooler `SUPABASE_DB_URL` and `engine\.venv\Scripts\python -c "...psycopg..."` or the SQL editor. Re-seeding is idempotent (upserts; strategy `status` is never overwritten).
4. **Secrets:** copy `.env.example` → `.env`, fill what the current phase needs. Never commit `.env`. Comments must be on their own lines (the dotenv loader treats a same-line `#` as part of the value). Check readiness any time:
   ```powershell
   cd engine; .\.venv\Scripts\python scripts\check_env.py     # OK / MISSING / FAIL per key, tests each one live, never prints secrets
   ```
5. **Stack:** `docker compose up -d` (engine on :8000, n8n on :5678, both in `America/New_York`).

## Regenerating the seed
Edit `engine/app/registry.py`, `engine/app/seed/*.py`, or `prompt-library/hermes-core/*.md`, then:
```powershell
cd engine; .\.venv\Scripts\python scripts\gen_seed.py
```
`pytest` fails if the SQL on disk is stale.

## Phase status
| Phase | Scope | Status |
|---|---|---|
| 0 | schema, seed, prompt library, engine skeleton | **done 2026-09-08** |
| 1 | `/data/*` endpoints, nightly same-feed pre-market baseline (IEX + SIP) with checkpoint profile, IEX↔SIP calibration log, Edge Function proxy code, n8n cron 01 | **done 2026-09-10** (Edge Function deployed) |
| 2 | `/scan/run` morning + midday, nightly `symbol_stats` universe (Alpaca), 9 detectors, n8n crons 02/03 | **done 2026-09-10** (Finviz Elite deferred; float = FMP shares outstanding, approximate) |
| 3 | sentiment pipeline → Ollama condenser → `news_sentiment` | next (Reddit keys still pending; Finnhub + Ollama ready) |
| 4 | Hermes decision node (templates 02/03/04) → `signals` | needs OpenRouter key |
| 5 | execution: Deploy webhook, Guardian gate, synthetic stop, 09:30 OCO, Telegram, kill switch | needs Telegram bot |
| 6 / 6.5 | journal, ledger, analytics, weekly autopsy → hypotheses, Guardian briefing | |
| 7 | backtest engine + S1–S8 baselines + S6 vs S7 | |
| 8 | hardening, onboarding, failure policy, tunnel, then ≥ 4-week paper month | |
| 9 | UI (React + Tailwind against the mockup) | |
| E1→E2→E5→FL→E3→E4 | extension modules, after the paper month | |

## Non-negotiables (from the spec)
Strategies are data, never code · LLMs never compute · keys never reach the frontend · pre-market = limit only, synthetic stop, no brackets · only `buying_power` (PDT fields are gone) · same feed on both sides of every relative-volume ratio (SIP baseline vs SIP live — decision #10) · paper trading before live · win rate is never the gate · not financial advice; tax provision is a display estimate.
