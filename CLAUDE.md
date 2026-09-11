# TradeHub — read this first

A personal, single-user systematic day-trading system. Scanner → news catalyst → Hermes trade plans →
owner-approved execution → journal → weekly self-review. Backend-first; the UI is Phase 9.

## Before doing anything

1. Read `docs/DECISIONS.md` — numbered decisions recording **why** the build deviates from the spec.
   The spec (`tradehub_implementation_plan.md`) is the design authority; DECISIONS.md is what was
   actually built and what was learned the hard way.
2. Read `app_phase_overview.md` — plain-language status of every phase.
3. Run `engine\.venv\Scripts\python scripts\check_env.py` to confirm credentials before touching
   anything that talks to a service.

Never redo a completed phase. Never re-apply migrations `0001`–`0005` (all applied). `0002_seed.sql` is
**generated** from `engine/app/registry.py` + `engine/app/seed/*.py` + `prompt-library/hermes-core/*.md`
by `engine/scripts/gen_seed.py` — edit the source and regenerate; a pytest fails when it is stale. Apply
only the statement you changed rather than re-running the whole seed, so no other setting is overwritten.

## Working with the owner

Single user in Portugal, **not a developer**. He runs the accounts and installs; you run the build.

- Give setup instructions in Windows/PowerShell terms. Never assume a Linux server or SSH.
- Expand spec shorthand on first use: "S1 (Low-Float Gapper)", "E2 (the Financial Analyst module,
  plan §36)", "template 04 (the one-sentence in-play test)". He knows the product, not the codes.
- Be brief. He has asked more than once for "few words, not unnecessary details".
- At the **end of every completed phase**: update `app_phase_overview.md` in plain language, commit the
  phase, and push to GitHub (https://github.com/Aimilios94/TradeHub, private). He expects this.

## Architecture in one paragraph

Thin n8n, fat Python (decision #1). n8n only schedules, alerts and receives webhooks — nine workflows in
`n8n/workflows/`, all ET. Every pipeline step is a FastAPI endpoint in `engine/app/<module>/router.py`.
Supabase holds all state. Ollama (`hermes3:8b`) runs natively on the host for the free, high-volume local
tier; OpenRouter (Hermes 70B/405B) does judgment. Docker runs `engine`, `n8n` and `monitor`.

## Rules that are not negotiable

- **Live trading is refused in code** (`app/broker/orders.assert_paper_only`) until Phase 8's paper month.
  Not a flag — an exception. Do not lift it without an explicit, separate, owner-approved commit.
- **Nothing executes without the owner's tap.** `auto_approve` exists, ships OFF, and the deploy path does
  not read it. Approval arrives as a Telegram button (workflow 04).
- **Fail safe, never fake** (plan §33). A missing figure is reported missing. A model answer that fails
  validation is stored rejected with its reasons, never silently corrected. An estimate is labelled
  `estimated`. Unknown is never green.
- **The Guardian may be tightened, never loosened.** A strategy's risk block can lower a global limit; it
  can never raise one. The weekly review discards any proposal that would.
- **Hermes proposes, the backtester disposes, the database remembers.** Nothing is auto-applied to a
  strategy. Hypotheses wait for a backtest and the owner's approval.

## Traps that have already cost time

- **Every prompt must be shown its own `output_schema`** (`llm.schema_contract`). Without it hosted models
  invent a plausible shape and 100% of answers fail validation.
- **Alpaca accepts a pre-market stop and never triggers it.** Silent acceptance, not rejection, is the
  danger — hence the synthetic stop monitor in its own container.
- **A paper account is never charged SEC/TAF.** The ledger estimates from `app_settings.fee_model` and
  marks the row.
- **Ollama host detection** uses `/.dockerenv`, not name resolution: `host.docker.internal` resolves on
  the host too, but nothing is listening there.
- **Alpaca free plan:** SIP refuses the last 15 minutes; never pass `limit` to `get_bars` (it caps total
  rows, not rows per symbol).
- **n8n CLI** (see `n8n/workflows/README.md`): prefix `docker exec` with `MSYS_NO_PATHCONV=1` in Git Bash;
  import ONE workflow per command; a re-import **deactivates** the workflow — re-arm with
  `n8n update:workflow --id=tradehub-wf-NN --active=true` then restart the container.

## Commands

```powershell
cd engine
.\.venv\Scripts\python -m pytest -q          # 238 tests, all offline
.\.venv\Scripts\python -m ruff check app tests
.\.venv\Scripts\python scripts\gen_seed.py   # after editing seed sources
.\.venv\Scripts\python scripts\check_env.py  # credential readiness, never prints secrets
```

```bash
docker compose up -d --build engine   # required after any engine code change
python n8n/build_import.py            # regenerate n8n import files after editing a workflow
```

Endpoints need `Authorization: Bearer <ENGINE_API_TOKEN>`. Console output is cp1252 on this machine —
prefix Python that prints em-dashes or emoji with `PYTHONIOENCODING=utf-8`.

## Never commit

`.env`, `n8n/data/` (holds the decrypted credential import and the n8n database), `supabase/.temp/`.
All three are gitignored — keep it that way. The repo is private; scan before pushing anything new.
