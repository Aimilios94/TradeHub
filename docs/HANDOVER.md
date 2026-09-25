# Where we left off

**Last updated: 2026-09-25 (paper month, day 11 of 28).**
Read this first on a new machine, then `CLAUDE.md`, then `docs/DECISIONS.md`.

---

## 1. The one-line answer

Phases 0–8 are **built, committed and pushed**. The engine has been running unattended on paper for
eleven days. Phase 9 (the dashboard) is next and has not been started. Nothing in the repo is
half-finished.

The paper month is **behind — but not because of the code.** See §5.

---

## 2. What a different PC needs

The repo is not enough on its own. Three things live outside git, on purpose:

| What | Where it is | How to get it onto the new PC |
|---|---|---|
| **`.env`** — every API key | Repo root, gitignored | **Copy it across yourself**, by hand, from the old PC. It is deliberately not in git and never will be. Without it nothing starts. |
| `n8n/data/` — the n8n database and its decrypted credential import | gitignored | Not needed. Re-run `python n8n/build_import.py` on the new PC and re-import; it rebuilds from `.env`. |
| `data/` — parquet bar cache, backups | gitignored | Not needed. The cache re-downloads free from Alpaca (it is ~180 MB, so expect a slow first backtest). |

**Setup on the new machine, in order:**

```powershell
git clone https://github.com/Aimilios94/TradeHub.git
cd TradeHub
# 1. copy your .env into the repo root NOW, before anything else

cd engine
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python scripts\check_env.py        # every key, probed live, never printed
.\.venv\Scripts\python -m pytest -q                # expect 393 passed, all offline

cd ..
docker compose up -d --build                       # engine + n8n + monitor
python n8n/build_import.py                         # writes n8n/data/import/ from .env
```

Then import the workflows **one at a time** (see `n8n/workflows/README.md` — a batch import fails on a
duplicate tag, and a re-import silently DEACTIVATES a workflow). Activate all eleven at
`http://localhost:5678`.

Do **not** re-apply migrations `0001`–`0006`. They are already applied to the live Supabase project and
re-running the seed would overwrite settings you have since edited. Supabase is cloud-hosted, so the new
PC talks to the same database the old one did — **the two machines must never run the engine at the
same time**, or both will scan, plan and journal into the same rows.

---

## 3. What is running right now

| Container | State | Job |
|---|---|---|
| `tradehub-engine` | up, healthy | every pipeline endpoint |
| `tradehub-monitor` | up | the sub-second synthetic pre-market stop |
| `tradehub-n8n` | up | eleven scheduled workflows, all ET |

Database: Supabase `mehjcmbypalshlgwhxme` (eu-west-2), migrations 0001–0006 applied, 20 `app_settings`
keys, and the live settings match `engine/app/seed/settings.py` exactly.

Broker: Alpaca **paper**, equity ~$100k. Live trading is refused in code
(`app/broker/orders.assert_paper_only`) and stays refused until the paper month finishes **and** a
separate, owner-approved commit lifts it.

---

## 4. The go-live scorecard (plan §17)

Measured, not remembered — `GET /ops/acceptance`, and in plain English on Telegram every Sunday.

| | Criterion | State on 2026-09-25 |
|---|---|---|
| 1 | Every strategy backtested | ✅ **met** — 7 of 7 (S2 excused: a briefing has no entry to replay) |
| 2 | 4 consecutive weeks on paper, ≥20 trades | ⬜ **1 of 4 weeks**, **0 of 20 trades** |
| 3 | Guardian drilled, ledger reconciles, tax provision | ✅ **met** — drill 10/10 |
| 4 | Deploy ≤2s, stop reaction <1s | ❓ **unknown** — no taps yet, so nothing to measure |
| 5 | Funding, W-8BEN, first strategy, accountant | 🙋 **yours** — the engine can never tick these |

Measured baselines (2026-03-02 → 2026-09-05), all in `backtests`:

| | Trades | Win rate | Expectancy | Verdict |
|---|---|---|---|---|
| **S1 Low-Float Gapper** | 42 | 42.9% | **+21.78** | inconclusive |
| S3 SMB Stocks-in-Play | 156 | — | −10.29 | fail |
| S4, S7, S8 | 82–215 | 16–38% | −53 to −79 | fail |
| S5, S6 | 45, 54 | 9%, 11% | −97, −79 | inconclusive (<100 trades) |

S1 is the only one in profit and **still may not be promoted**: 42 trades is far under the 100 required,
and walk-forward found the edge in only one half of the window. None of the seven is a `pass`.

---

## 5. Why the paper month is behind — read this one properly

The pipeline is healthy. Scans have been running and finding candidates every day they run:
450–660 symbols evaluated, 54–92 passing. That part is fine.

**The problem is that the machine is not awake when the 08:00 ET cron fires.**

Scan coverage since day zero (2026-09-14):

```
Mon 09-14  NO SCAN        Mon 09-21  NO SCAN
Tue 09-15  scan           Tue 09-22  scan
Wed 09-16  scan           Wed 09-23  scan
Thu 09-17  scan           Thu 09-24  partial (S2 only)
Fri 09-18  scan           Fri 09-25  scan
```

And `/signals/plan` — the step that actually writes trade plans — ran on only **3 of the 8 scan days**.
The three briefings it produced are stamped **13:08, 10:50 and 00:22 ET**, not 08:00. That is n8n
running a missed schedule whenever the machine next comes up, not a fault in the chain.

Consequences, in order of importance:

1. **A week only counts with scans on ≥4 of its days.** Week 1 scraped through on exactly 4. A week with
   three missed mornings resets the four-week streak to zero — and the streak is criterion 2.
2. **One signal has reached you in eleven days** (IONQ, 2026-09-23) and it is *still sitting
   `proposed`* — never deployed, never cancelled. That is why there are 0 of the 20 trades, and why
   criterion 4 has nothing to measure.
3. **Workflow 11 was never activated.** There has been exactly one backup, the manual one from
   2026-09-11. No nightly backup and no Sunday scorecard have run.

**What fixes it (all on your side, none of it code):**

- Keep the PC on and awake at 08:00 ET = **13:00 Portugal**. Turn off sleep/hibernate, and make sure
  Docker Desktop starts with Windows.
- **Activate workflow 11** at `http://localhost:5678`.
- Tap **Deploy or Cancel** on signals when they arrive. Twenty deployed-and-closed trades is a hard
  requirement, and no amount of waiting produces them.
- If the streak has already broken by the time you read this, restart the clock deliberately:
  `POST /ops/paper-month/start` (stamp a **Monday**).

---

## 6. Open items

**Code — none blocking.** Nothing is half-built. Two things fixed on 2026-09-25 (decisions #97–#98):
`broker_account.snapshot()` had no retry despite being the most-called external read, and a single
transient failed poll was being recorded as a red incident. Both were producing false failures against
criterion 2; the 7 historic ones have been acknowledged.

**Owner:**
- Activate workflow 11, keep the machine awake at 13:00 local, act on signals (see §5)
- Reddit keys (optional — then add `"reddit"` to `app_settings.sentiment.sources`, no code change)
- Funding, W-8BEN, first live strategy, accountant (criterion 5)
- Finviz Elite deferred — paid, only affects exact float numbers

**Known and deliberate:**
- No IEX↔SIP calibration factor has been logged yet, so the backtest countermeasure reports it unknown
  and leaves relative-volume thresholds alone rather than assuming 1.0
- `backtests` rows written before 2026-09-11 show `symbol_days_screened/_passed: 0` — those counters
  were dead until then; read `diagnostics.funnel` on the old rows instead

---

## 7. Next: Phase 9 — the dashboard

Not started. Build against `dashboard_mockup.html` (§32) — React + Tailwind, no design-tool dependency
(§38). The section list and sidebar order are in the plan (§15) and `APP_OVERVIEW.md`.

The two rules that shape it: **the frontend never holds a key** (reads via the Supabase Edge proxy,
actions via authenticated n8n webhooks), and **the signal card is the loudest thing on screen**, because
it is the only element that asks you to do something.

It runs alongside the paper month by design — four weeks of waiting, four weeks of work.

---

## 8. Commands worth having

```powershell
cd engine
.\.venv\Scripts\python -m pytest -q                      # 393 tests, all offline
.\.venv\Scripts\python scripts\check_env.py              # credentials, probed live
.\.venv\Scripts\python scripts\onboarding.py --check     # keys, risk limits, active strategies
.\.venv\Scripts\python scripts\backup.py --list          # what backups exist
.\.venv\Scripts\python scripts\restore.py <folder>       # dry run; --yes to write
```

```
GET  /ops/acceptance     the go-live scorecard, measured
GET  /ops/preflight      may it trade right now, and if not which check says no
GET  /ops/incident-log   what has been going wrong, and what keeps recurring
POST /ops/drill          force all ten Guardian checks to block (submits nothing)
POST /ops/backup         take a backup now
```

Every endpoint needs `Authorization: Bearer <ENGINE_API_TOKEN>`.
Console output here is cp1252 — prefix Python that prints em-dashes or emoji with `PYTHONIOENCODING=utf-8`.
