# TradeHub

Personal, single-user trading hub. **Hermes proposes, the Python engine proves, you decide.**
**Where we left off: [docs/HANDOVER.md](docs/HANDOVER.md)** — read this first on a new machine.

Plain-language map: [APP_OVERVIEW.md](APP_OVERVIEW.md) · Full spec: [tradehub_implementation_plan.md](tradehub_implementation_plan.md) (v1.3) · UI reference: [dashboard_mockup.html](dashboard_mockup.html) · Decisions taken during the build: [docs/DECISIONS.md](docs/DECISIONS.md)

## Architecture (thin n8n, fat Python)
- **`engine/`** — Python 3.12 FastAPI service. All rules, math, scanning, Guardian gate, execution, backtests, analytics. Every pipeline step is an endpoint with tests.
- **n8n** (Docker) — crons in ET, Telegram, inbound webhooks. Calls engine endpoints; holds no logic.
- **Supabase** — Postgres (`supabase/migrations/`), Edge Function market proxy (Phase 1). All state lives here.
- **Ollama** (native Windows, GPU) — Hermes 3 8B for bulk summarization. **OpenRouter** — Hermes 3 70B/405B for judgment.
- **`prompt-library/`** — every strategy, framework, and prompt; the DB seed is generated from it.

## Repository layout
```
engine/            FastAPI service — app/{registry,models,config,db,auth,notify,main}.py, app/seed/, app/marketdata/ (Phase 1), app/scanner/ + app/detectors/basic.py (Phase 2), app/llm/ + app/sentiment/ (Phase 3), app/signals/ + app/broker/ (Phase 4), app/guardian/ + app/execution/ + app/monitor/ (Phase 5), app/journal/ + app/analytics/ + app/review/ (Phase 6), app/guardian/{briefing,rules}.py (Phase 6.5), app/detectors/{indicators,structure}.py + app/exits/plugins.py + app/backtest/ (Phase 7), app/ops/ (Phase 8)
  scripts/gen_seed.py   generates supabase/migrations/0002_seed.sql (single source of truth = Python + prompt-library)
  scripts/apply_sql.py  applies a migration, or only the seed statements that changed (--grep, --dry-run)
  tests/                pytest
supabase/migrations/   0001_core_schema.sql · 0002_seed.sql (GENERATED) · 0003_premarket_profile.sql · 0004_symbol_stats.sql · 0005_briefings.sql · 0006_backtest_spec.sql
supabase/functions/    market-proxy (Deno Edge Function — the frontend's only market-data door)
prompt-library/        strategies/ frameworks/ hermes-core/ hermes-extensions/ extension-modules/ tests/ _originals/
n8n/workflows/         exported workflow JSON — 11 workflows, one per scheduled job (see its own README)
data/parquet/          deep historical bars for the backtester (gitignored; shared by the host and the engine container)
docker-compose.yml     engine + n8n + monitor
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
   # every endpoint below /data, /scan, /enrich, /signals, /execute, /journal, /analytics, /review needs:
   #   -H "Authorization: Bearer <ENGINE_API_TOKEN>"
   #   e.g. GET /data/premarket/PLUG · POST /scan/run?kind=morning&dry_run=true · GET /scan/latest · GET /scan/universe/PLUG
   #        GET /enrich/status · POST /enrich/run?symbols=AMD&dry_run=true · GET /enrich/AMD
   #        GET /signals/status · POST /signals/plan?dry_run=true · GET /signals/latest · GET /signals/briefing
   #        GET /execute/status · POST /execute/gate/preview?signal_id=… · POST /execute/deploy?signal_id=…
   #        POST /execute/kill-switch?engage=true · GET /execute/orders · GET /execute/compliance
   #        POST /journal/eod · GET /analytics/report · GET /analytics/ledger · POST /review/weekly
   #        POST /guardian/briefing · GET /guardian/state · POST /guardian/rules-check · GET /guardian/rules
   ```
3. **Database:** create a Supabase project → apply `supabase/migrations/` in order (0001 schema, 0002 seed, 0003 pre-market profile, 0004 symbol_stats, 0005 briefings, 0006 backtest spec). From this PC the session-pooler `SUPABASE_DB_URL` is required — the direct `db.<ref>` host is IPv6-only and will not resolve. Apply with:
   ```powershell
   cd engine; .\.venv\Scripts\python scripts\apply_sql.py ..\supabase\migrations\0006_backtest_spec.sql
   ```
   Re-seeding is idempotent (upserts; strategy `status` is never overwritten), but re-running the WHOLE seed overwrites every `app_settings` row including ones you have edited — so apply only what changed:
   ```powershell
   .\.venv\Scripts\python scripts\apply_sql.py --grep "insert into strategy_configs" ..\supabase\migrations\0002_seed.sql
   ```
   Add `--dry-run` to see which statements would run.
4. **Secrets:** copy `.env.example` → `.env`, fill what the current phase needs. Never commit `.env`. Comments must be on their own lines (the dotenv loader treats a same-line `#` as part of the value). Check readiness any time:
   ```powershell
   cd engine; .\.venv\Scripts\python scripts\check_env.py     # OK / MISSING / FAIL per key, tests each one live, never prints secrets
   ```
5. **Stack:** `docker compose up -d` — engine on :8000, n8n on :5678, and `monitor` (the synthetic
   pre-market stop, no port) — all in `America/New_York`. The monitor is a separate container on
   purpose: rebuilding the engine must never take the stop down while a position is open.

## Broker rules
`docs/BROKER_RULES.md` lists the six broker rules the engine depends on and where each came
from. The monthly job (`POST /guardian/rules-check`, n8n cron 09) hashes those source pages and raises a
`compliance_events` row when one moves. It flags; you read the page and decide. After applying a change,
re-run with `rebaseline=true`.

## Regenerating the seed
Edit `engine/app/registry.py`, `engine/app/seed/*.py`, or `prompt-library/hermes-core/*.md`, then:
```powershell
cd engine; .\.venv\Scripts\python scripts\gen_seed.py
```
`pytest` fails if the SQL on disk is stale.

## n8n workflows
`python n8n/build_import.py` renders the CLI import files from `n8n/workflows/*.json` + `ENGINE_API_TOKEN`,
then import them **one file per command** and activate them in the UI. See `n8n/workflows/README.md` for the
three gotchas (batch tag collision, Git Bash path mangling, imports arrive inactive).

## Phase status
| Phase | Scope | Status |
|---|---|---|
| 0 | schema, seed, prompt library, engine skeleton | **done 2026-09-08** |
| 1 | `/data/*` endpoints, nightly same-feed pre-market baseline (IEX + SIP) with checkpoint profile, IEX↔SIP calibration log, Edge Function proxy code, n8n cron 01 | **done 2026-09-10** (Edge Function deployed) |
| 2 | `/scan/run` morning + midday, nightly `symbol_stats` universe (Alpaca), 9 detectors, n8n crons 02/03 | **done 2026-09-10** (Finviz Elite deferred; float = FMP shares outstanding, approximate) |
| 3 | `/enrich/run`: Finnhub + Yahoo RSS → local `hermes3:8b` condenser → `news_sentiment`, catalyst written back onto `scans`; n8n cron 02 chains it after the morning scan | **done 2026-09-10** (Reddit implemented but inert until its keys arrive) |
| 4 | `/signals/plan`: in-play gate (template 04) → trade plans (02) → `signals`, watchlist briefing (03) → `briefings`; every planned price re-validated in code | **done 2026-09-10** (proposals only — nothing executes) |
| 5 | `/execute/*`: Telegram Deploy tap → enforcing Guardian Layer-1 gate → limit order → `orders`; synthetic pre-market stop in its own container; 09:31 OCO attach; kill switch | **done 2026-09-10** (paper only — live is refused in code until Phase 8) |
| 6 | `/journal/eod` (FIFO round trips + Guardian Layer-2 ledger), `/analytics/report` (§14), `/review/weekly` (templates 05+06 → `hypotheses`), degradation trigger; n8n crons 06/07 | **done 2026-09-10** (proposals only — nothing is auto-applied) |
| 6.5 | `/guardian/briefing` (Layer 3, plain-language state report, reachable as `/guardian` in Telegram) + `/guardian/rules-check` (monthly source-page freshness → `compliance_events`); `docs/BROKER_RULES.md`; n8n crons 08/09 | **done 2026-09-10** |
| 7 | `/backtest/*`: scanner-universe replay with the four §13 countermeasures (survivorship coverage, IEX↔SIP calibration, an explicit pessimistic fill model, min-sample + walk-forward guards); the remaining 23 detectors and 9 exit plugins, so the registry is fully implemented; per-strategy `BacktestSpec` (migration 0006); parquet bar cache; hypothesis grading + `/backtest/promote`; n8n cron 10 | **done 2026-09-11** (baselines recorded for S4–S8; **S1 and S3 full baselines still to run** — see below. Nothing is auto-applied; promotion needs a passing backtest and live is still refused in code) |
| 8 | `app/ops/*`: retry + per-provider rate-limit guards (reads only — an order submission is never auto-retried), §33 failure policy as blocking checks in front of the Guardian gate, one central failure recorder, the Guardian fire drill, the measured §17 go-live scorecard, nightly backup + checksum-verified restore, `scripts/onboarding.py`; n8n cron 11 | **hardening done 2026-09-11**; paper month running since **2026-09-14** (day zero stamped on a weekday deliberately — decision #96). Cloudflare/Tailscale tunnel deferred to Phase 9, when the UI needs it |
| 9 | UI (React + Tailwind against the mockup) | **next** — runs alongside the paper month |
| E1→E2→E5→FL→E3→E4 | extension modules, after the paper month | |

## Backtest baselines (first measured, 2026-09-11)
`2026-03-02 → 2026-09-05`, SPY/QQQ, $25k equity, 0.5% risk per trade, stored in `backtests`:

| | trades | win rate | expectancy | verdict |
|---|---|---|---|---|
| S1 Low-Float Gapper | 42 | 42.9% | **+21.78** | inconclusive (under 100 trades; edge in one half only) |
| S3 SMB Stocks-in-Play | 156 | — | −10.29 | **fail** |
| S4 HPS Quad Divergence | 150 | 34.0% | −53.05 | **fail** |
| S5 20/20 Bull Flag | 45 | 8.9% | −96.99 | inconclusive (under 100 trades) |
| S6 Bear Flag (PDF canonical) | 54 | 11.1% | −79.06 | inconclusive (under 100 trades) |
| S7 Bear Flag (interview variant) | 215 | 15.8% | −74.21 | **fail** |
| S8 HPS Swing Divergence | 82 | 37.8% | −79.23 | **fail** |

**S1 is the only one that makes money** (+21.78 per trade after modelled fees and spread, profit factor
1.38) and it is still `inconclusive`, for two separate reasons that both matter: 42 trades is well under
the 100 §20 requires, and the walk-forward split found positive expectancy in only one of the two halves
— an edge that lives in half a window is a candidate for a coincidence. The other six lose money after
costs, against the source PDF's claimed ~95% win rates, which is why win rate is never the gate. **Read them with their limits attached:** one six-month window of one market regime,
and the entries are the deterministic stand-ins each strategy declares in its `backtest` block
(decision #60), not the Hermes plans the live system produces. They are not grounds to retire a
strategy; they are grounds to promote none of it. **S6 vs S7** (spec §17 criterion 1): S7 is less bad
per trade but lost four times as much in total; both losing, so no winner. **S5's optional ATR target
could not be calibrated** — 43 of its 45 trades were stopped out and the target was never reached.

All seven backtestable strategies now have a baseline over the same window (S1 and S3 added 2026-09-11
during Phase 8 — S1's screening pass covered 39,061 symbol-days to produce 133 candidate days and 42
trades). S2 is correctly absent: a briefing has no entry, stop or size, so there is nothing to replay,
and the run says so rather than skipping silently. **None may be promoted** — `/backtest/promote`
requires a `pass`, and nothing here is one.

## Phase 8 operations (`/ops/*`)

| Endpoint | What it answers |
|---|---|
| `GET /ops/preflight` | May the engine open a position right now, and if not, which check says no |
| `GET /ops/quote-age` | How old the newest IEX quote is, against `failure_policy.data_stale_seconds` |
| `GET /ops/rate-limits` | The pacing the engine applies to itself, per provider |
| `POST /ops/drill` | Force all ten Guardian checks to block, once (§17.3). Submits nothing |
| `GET /ops/acceptance` | The §17 gate to real money, measured. Grants nothing |
| `POST /ops/paper-month/start` | Stamp day zero of the four-week clock |
| `GET /ops/reconcile` | Is every ledger fee row backed by an activity id or labelled `estimated` |
| `POST /ops/backup` · `GET /ops/backups` | Take / list a backup (restore is CLI only) |
| `GET /ops/failures` · `GET /ops/incident-log` | What has been going wrong, and what keeps recurring |
| `POST /ops/selftest` · `POST /ops/test-alert` | Connectivity and the alert path, end to end |

Three properties worth knowing before changing anything here:

- **`unknown` blocks exactly as `red` does.** A check that could not be evaluated never passes — "we
  could not reach the broker, assume it is fine" is the failure §33 was written against. Blocking is
  asymmetric: a red stops a new ENTRY, never an exit, a reconciliation or the synthetic stop.
- **Only reads are retried.** `broker.orders.submit` is deliberately outside `ops/resilience`, because a
  timeout cannot be told apart from a silent fill; a test reads its source to prove no retry wrapper has
  appeared. Recovery is `/execute/orders/sync`, not a second attempt.
- **The scorecard cannot grant permission.** Criterion 5 is the owner's permanently, and live trading
  stays refused in `app/broker/orders.assert_paper_only` regardless (decision #37).

## Non-negotiables (from the spec)
Strategies are data, never code · LLMs never compute · keys never reach the frontend · pre-market = limit only, synthetic stop, no brackets · only `buying_power` (PDT fields are gone) · same feed on both sides of every relative-volume ratio (SIP baseline vs SIP live — decision #10) · paper trading before live · win rate is never the gate · not financial advice; tax provision is a display estimate.
