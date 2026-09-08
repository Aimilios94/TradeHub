# n8n workflows (exported JSON)

n8n is a thin scheduler here: crons in `America/New_York`, Telegram, inbound webhooks. Each workflow calls an engine endpoint and forwards the result. No business logic in nodes.

Planned workflows (one file each, added per phase):

| File | Trigger (ET) | Calls | Phase |
|---|---|---|---|
| `01_premarket_baseline_nightly.json` | cron 23:00 | `POST /data/premarket-baseline/run` | 1 |
| `02_scan_morning.json` | cron 08:00 | `POST /scan/run?kind=morning` → `POST /enrich/run` → `POST /signals/plan` → Telegram | 2–4 |
| `03_scan_midday.json` | cron 11:30 | `POST /scan/run?kind=midday` | 2 |
| `04_deploy_webhook.json` | webhook (HMAC + JWT) | `POST /execute/deploy` | 5 |
| `05_oco_attach_0930.json` | cron 09:30 | `POST /execute/attach-oco` | 5 |
| `06_eod_journal.json` | cron 16:30 | `POST /journal/eod` | 6 |
| `07_weekly_autopsy.json` | cron Sun 09:00 | `POST /review/weekly` | 6 |
| `08_guardian_briefing.json` | webhook (button) | `POST /guardian/briefing` | 6.5 |
| `09_rules_freshness_monthly.json` | cron 1st 10:00 | `POST /guardian/rules-check` | 6.5 |
| `10_telegram_alerts.json` | webhook from engine | Telegram | 5 |

Import via n8n UI → Workflows → Import from file, or mount `./n8n/workflows` (already in `docker-compose.yml`).
