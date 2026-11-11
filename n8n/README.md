# n8n workflows

n8n only triggers jobs on a schedule. It holds no logic: each workflow is a Schedule Trigger followed by one HTTP Request to the API.

- UI: http://127.0.0.1:5680 (create the owner account on first open).
- Inside the Compose network the API is `http://api:8000`.

## Calling a job

```
POST http://api:8000/jobs/<job_name>
Header: X-Trigger-Secret: {{ $env.N8N_TRIGGER_SECRET }}
```

The secret comes from `.env` and is passed into the n8n container by Compose.

## Jobs

| Job | What it does | Since |
|---|---|---|
| `selfcheck` | Confirms n8n can reach the API and the API can reach the database. Writes nothing | Phase 0 |
| `check_sources` | One real call per source adapter; updates source health | Phase 0.5 |
| `record_news` | Headlines for the watchlist from Finnhub and Yahoo RSS into `news_items` | Phase 0.5 |
| `record_bars` | Daily bars for every seeded ticker and intraday bars for the watchlist into `data/live/` | Phase 0.5 |
| `record_cot` | CFTC COT history into `data/live/cot/` | Phase 0.5 |

A job answers 200 when everything worked and 502, with the same body, when anything failed. A job that is already running answers 409.

## Workflows

| File | Job | Schedule (UTC) |
|---|---|---|
| `record_news.json` | `record_news` | 07:00, 13:00 and 21:30 every day |
| `record_bars.json` | `record_bars` | 22:30 every day |
| `record_cot.json` | `record_cot` | Friday 22:00 |

The n8n container mounts this folder at `/workflows`.

Import (also updates workflows already imported, and unpublishes them):

```
docker compose exec n8n n8n import:workflow --separate --input=/workflows
```

Then publish each workflow once in the editor: open http://127.0.0.1:5680, open the workflow, click **Publish**. The command line cannot do this step. A workflow runs on its schedule only while it is published.

After changing a workflow in the editor, export it back into this folder so the repo matches.
