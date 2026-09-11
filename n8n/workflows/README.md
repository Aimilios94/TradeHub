# n8n workflows (exported JSON)

n8n is a thin scheduler here: crons in `America/New_York`, Telegram, inbound webhooks. Each workflow calls an engine endpoint and forwards the result. No business logic in nodes.

Planned workflows (one file each, added per phase):

| File | Trigger (ET) | Calls | Phase |
|---|---|---|---|
| `01_premarket_baseline_nightly.json` | cron 23:00 Mon–Fri | `POST /data/premarket-baseline/run?days=1&notify=true` → `POST /scan/universe/run` | 1–2 ✅ |
| `02_scan_morning.json` | cron 08:00 Mon–Fri | `/scan/run?kind=morning` → `/enrich/run` → `/signals/plan` → `/execute/push-signals` | 2–5 ✅ |
| `03_scan_midday.json` | cron 11:30 Mon–Fri | `POST /scan/run?kind=midday` | 2 ✅ |
| `04_deploy_telegram.json` | Telegram button tap | `POST /execute/telegram/callback` → gate → order | 5 ✅ |
| `05_oco_attach_0930.json` | cron 09:31 Mon–Fri | `POST /execute/oco/attach` → `POST /execute/orders/sync` | 5 ✅ |
| `06_eod_journal.json` | cron 16:30 Mon–Fri | `POST /execute/orders/sync` → `POST /journal/eod` | 6 ✅ |
| `07_weekly_review.json` | cron Sun 09:00 | `POST /review/weekly` (templates 05+06 → `hypotheses`) | 6 ✅ |
| `08_guardian_briefing.json` | Telegram `/guardian [question]` | `POST /guardian/briefing` | 6.5 ✅ |
| `09_rules_freshness_monthly.json` | cron 1st 10:00 ET | `POST /guardian/rules-check` | 6.5 ✅ |
| `10_backtest_queue_weekly.json` | cron Sun 11:00 ET | `POST /backtest/queue/run` | 7 ✅ |
| ~~`10_telegram_alerts.json`~~ | webhook from engine | Telegram | never built — the engine sends its own alerts directly (`app/notify.py`), so that number was reused above |

These files are the design source of truth. Deployment state (the workflow `id`, `active`) is not tracked
here — `n8n/build_import.py` adds a stable id so a re-import updates in place instead of duplicating.

## Importing (CLI, no UI clicking)

```bash
python n8n/build_import.py          # renders n8n/data/import/ from these files + ENGINE_API_TOKEN
# then, one file at a time (see the note below):
MSYS_NO_PATHCONV=1 docker exec tradehub-n8n n8n import:credentials --input=/home/node/.n8n/import/credentials.json
MSYS_NO_PATHCONV=1 docker exec tradehub-n8n n8n import:workflow    --input=/home/node/.n8n/import/workflows/02_scan_morning.json
docker restart tradehub-n8n
```

`python n8n/build_import.py --check` fails if the rendered files are stale.

Three things that will bite otherwise, all verified 2026-09-10:

1. **Import one workflow per command.** `n8n import:workflow` creates every tag it sees without
   de-duplicating inside a batch, so two workflows sharing a tag (02 and 03 both carry `phase-2`) abort the
   whole import with `SQLITE_CONSTRAINT: UNIQUE constraint failed: tag_entity.name`. One file at a time
   reuses the existing tag and works.
2. **Git Bash rewrites container paths** — `/home/node/...` becomes `C:/Program Files/Git/home/node/...`
   and the import fails with ENOENT. Prefix with `MSYS_NO_PATHCONV=1`.
3. **Imported workflows arrive INACTIVE**, and re-importing an active workflow deactivates it (the CLI
   prints "Deactivating workflow ..."). Re-arm it without the UI with
   `docker exec tradehub-n8n n8n update:workflow --id=tradehub-wf-02 --active=true`, then
   `docker restart tradehub-n8n` — the update only takes effect on restart, which the CLI warns about.

The credential is written decrypted; `n8n import:credentials` encrypts it with the instance key on the way
in. To confirm it decrypted to the right token without printing it, compare hashes:
`docker exec tradehub-n8n n8n export:credentials --all --decrypted --output=/tmp/c.json`, hash `data.value`,
compare with `sha256("Bearer " + ENGINE_API_TOKEN)`, then delete the file.

Engine URL inside Docker is `http://engine:8000`; from the Windows host it is `http://localhost:8000`.
